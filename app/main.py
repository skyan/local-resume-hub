from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates

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
