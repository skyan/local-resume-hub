# AGENTS

This document guides human and AI contributors working on `local-resume-hub`.

## Purpose

`local-resume-hub` is a local-first resume indexing system:

- Recursively watches a resume directory
- Extracts candidate fields from PDF/images
- Infers applied position from filename
- Optionally enhances extraction with LLM
- Stores normalized records in SQLite
- Serves a web UI for search/sort/pagination/progress

## Core Modules

- `app/main.py`: FastAPI app and HTTP routes
- `app/pipeline.py`: ingest queue, watcher, periodic scan, progress
- `app/extractors.py`: OCR/text extraction and local field parsing
- `app/llm.py`: optional LLM enhancement adapter
- `app/db.py`: SQLite schema and query layer
- `templates/index.html`: web UI
- `scripts/service.sh`: start/stop/restart/status/reset-db

## Development Rules

- Keep the app local-first and privacy-first; avoid cloud dependencies by default.
- Do not commit personal resume files, databases, logs, or `.env`.
- Favor deterministic parsing before LLM fallback.
- Preserve backward compatibility for SQLite schema changes (`ALTER TABLE` migration style).
- Add/adjust tests in `tests/` when parsing logic changes.

## Common Commands

```bash
# install
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# run
./scripts/run.sh

# managed service
./scripts/service.sh start
./scripts/service.sh status
./scripts/service.sh reset-db

# tests
pytest -q
```

## Security Checklist Before Release

- Confirm `.gitignore` excludes `.env`, `logs/`, `run/`, `data/*.db`, `.venv/`
- Ensure `.env.example` has placeholders only
- Run secret scan for API keys and private tokens
- Avoid logging full sensitive content in production
