from __future__ import annotations

import datetime as dt
import logging
import shutil
import subprocess
from os import access, W_OK
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.config import load_settings
from app.db import Database
from app.pipeline import ResumeIngestionService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


settings = load_settings()
db = Database(settings.db_path)
service = ResumeIngestionService(settings, db)

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
app = FastAPI(title="本地简历管理器", version="0.1.0")
logger = logging.getLogger(__name__)


class DirPayload(BaseModel):
    target_dir: str = Field(min_length=1)


class ExportPayload(BaseModel):
    candidate_ids: list[int] = Field(default_factory=list)
    target_dir: str = Field(min_length=1)


class CandidateUpdatePayload(BaseModel):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    education: str | None = None
    years_experience: str | None = None
    skills: str | None = None
    applied_position: str | None = None


@app.on_event("startup")
async def startup() -> None:
    db.init_schema()
    await service.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    await service.stop()


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "resume_root": str(settings.resume_root),
        "db_path": str(settings.db_path),
        "llm_enabled": bool(settings.enable_llm_enhance and settings.dashscope_api_key),
    }


@app.get("/api/progress")
async def progress() -> dict:
    return await service.get_progress()


@app.get("/api/positions")
async def list_positions() -> dict:
    return {"items": db.list_positions()}


@app.get("/api/candidates")
async def list_candidates(
    q: str | None = None,
    name: str | None = None,
    position: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort_by: str = Query("created_at", pattern="^(created_at|name|position)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> dict:
    from_ts = _to_ts(date_from)
    to_ts = _to_ts(date_to, end_of_day=True)

    rows, total = db.list_candidates(
        q=q,
        name=name,
        position=position,
        date_from=from_ts,
        date_to=to_ts,
        sort_by=sort_by,
        order=order,
        page=page,
        page_size=page_size,
    )
    return {
        "items": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "sort_by": sort_by,
        "order": order,
    }


@app.get("/api/candidates/{candidate_id}")
async def get_candidate(candidate_id: int) -> dict:
    rec = db.get_candidate(candidate_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return rec


@app.put("/api/candidates/{candidate_id}")
async def update_candidate(candidate_id: int, payload: CandidateUpdatePayload) -> dict:
    rec = db.get_candidate(candidate_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Candidate not found")

    data = payload.model_dump()
    ok = db.update_candidate_fields(candidate_id, data)
    if not ok:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    updated = db.get_candidate(candidate_id)
    return {"ok": True, "item": updated}


@app.delete("/api/candidates/{candidate_id}")
async def delete_candidate(candidate_id: int) -> dict:
    removed = db.delete_candidate_and_resume(candidate_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Candidate not found")

    file_path = Path(removed["file_path"])
    file_deleted = False
    file_error = None
    try:
        if file_path.exists() and file_path.is_file():
            file_path.unlink()
            file_deleted = True
    except Exception as e:
        file_error = f"file_delete_failed:{type(e).__name__}"
        logger.warning("delete_candidate file remove failed candidate_id=%s path=%s err=%s", candidate_id, file_path, e)

    return {
        "ok": True,
        "deleted_candidate_id": candidate_id,
        "file_path": str(file_path),
        "file_deleted": file_deleted,
        "file_error": file_error,
    }


@app.get("/api/candidates/{candidate_id}/file")
async def open_candidate_file(candidate_id: int) -> FileResponse:
    rec = db.get_candidate(candidate_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Candidate not found")

    path = Path(rec["file_path"])
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Resume file not found")

    media_type = _media_type_for_path(path)
    return FileResponse(
        path=path,
        media_type=media_type,
        headers={
            "Content-Disposition": "inline",
        },
    )


@app.get("/favicon.ico")
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/.well-known/appspecific/com.chrome.devtools.json")
async def chrome_devtools_probe() -> Response:
    return Response(status_code=204)


@app.post("/api/rescan")
async def rescan() -> dict:
    await service.enqueue_existing_files()
    return {"status": "queued"}


@app.post("/api/validate-dir")
async def validate_dir(payload: DirPayload) -> dict:
    ok, writable, reason, resolved = _validate_target_dir(payload.target_dir)
    return {
        "ok": ok,
        "writable": writable,
        "reason": reason,
        "resolved_path": str(resolved) if resolved else None,
    }


@app.post("/api/export-resumes")
async def export_resumes(payload: ExportPayload) -> dict:
    if not payload.candidate_ids:
        raise HTTPException(status_code=400, detail="candidate_ids is required")

    ok, writable, reason, target_dir = _validate_target_dir(payload.target_dir)
    if not ok or not writable or target_dir is None:
        raise HTTPException(status_code=400, detail=f"invalid target_dir: {reason}")

    uniq_ids = sorted(set(payload.candidate_ids))
    source_items = db.list_candidate_files_by_ids(uniq_ids)
    source_by_id = {int(i["candidate_id"]): i for i in source_items}

    results: list[dict] = []
    success = 0
    failed = 0

    for cid in uniq_ids:
        item = source_by_id.get(cid)
        if item is None:
            failed += 1
            results.append(
                {
                    "candidate_id": cid,
                    "ok": False,
                    "error": "candidate_not_found",
                    "source_path": None,
                    "dest_path": None,
                }
            )
            continue

        src = Path(str(item["file_path"]))
        dest = target_dir / src.name
        if not src.exists() or not src.is_file():
            failed += 1
            results.append(
                {
                    "candidate_id": cid,
                    "ok": False,
                    "error": "source_file_not_found",
                    "source_path": str(src),
                    "dest_path": str(dest),
                }
            )
            continue

        try:
            shutil.copy2(src, dest)
            success += 1
            results.append(
                {
                    "candidate_id": cid,
                    "ok": True,
                    "error": None,
                    "source_path": str(src),
                    "dest_path": str(dest),
                }
            )
        except Exception as e:
            failed += 1
            results.append(
                {
                    "candidate_id": cid,
                    "ok": False,
                    "error": f"copy_failed:{type(e).__name__}",
                    "source_path": str(src),
                    "dest_path": str(dest),
                }
            )

    return {
        "total": len(uniq_ids),
        "success": success,
        "failed": failed,
        "target_dir": str(target_dir),
        "items": results,
    }


@app.post("/api/open-target-dir")
async def open_target_dir(payload: DirPayload) -> dict:
    ok, writable, reason, target_dir = _validate_target_dir(payload.target_dir)
    if not ok or not writable or target_dir is None:
        raise HTTPException(status_code=400, detail=f"invalid target_dir: {reason}")

    try:
        subprocess.run(["open", str(target_dir)], check=True)
        return {"ok": True, "opened": True, "reason": None}
    except Exception as e:
        logger.warning("open_target_dir failed: %s", e)
        return {"ok": False, "opened": False, "reason": f"open_failed:{type(e).__name__}"}


@app.post("/api/pick-target-dir")
async def pick_target_dir() -> dict:
    script = 'set p to POSIX path of (choose folder with prompt "选择导出目录")\nreturn p'
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as e:
        logger.warning("pick_target_dir failed: %s", e)
        return {"ok": False, "picked": False, "reason": f"pick_failed:{type(e).__name__}"}

    if proc.returncode != 0:
        # User cancelled or AppleScript runtime error.
        return {
            "ok": False,
            "picked": False,
            "reason": "pick_cancelled_or_failed",
            "stderr": (proc.stderr or "").strip(),
        }

    path = (proc.stdout or "").strip()
    if not path:
        return {"ok": False, "picked": False, "reason": "empty_path"}

    return {"ok": True, "picked": True, "target_dir": path}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "title": "本地简历管理器",
            "resume_root": str(settings.resume_root),
        },
    )


def _to_ts(value: str | None, end_of_day: bool = False) -> int | None:
    if not value:
        return None
    try:
        d = dt.datetime.strptime(value, "%Y-%m-%d")
        if end_of_day:
            d = d.replace(hour=23, minute=59, second=59)
        return int(d.timestamp())
    except ValueError:
        return None


def _media_type_for_path(path: Path) -> str:
    ext = path.suffix.lower()
    explicit = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }
    return explicit.get(ext, "application/octet-stream")


def _validate_target_dir(target_dir: str) -> tuple[bool, bool, str | None, Path | None]:
    try:
        p = Path(target_dir).expanduser()
    except Exception:
        return False, False, "invalid_path", None

    if not p.is_absolute():
        return False, False, "path_must_be_absolute", None
    if not p.exists():
        return False, False, "dir_not_exists", None
    if not p.is_dir():
        return False, False, "not_a_directory", None
    if not access(str(p), W_OK):
        return True, False, "dir_not_writable", p
    return True, True, None, p.resolve()
