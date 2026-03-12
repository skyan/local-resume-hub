# 本地简历管理器 (local-resume-hub)

本项目是一个本地优先的简历管理系统，用于递归扫描指定目录中的简历文件（PDF/图片），自动提取候选人信息并提供 Web 查询能力。

## 功能特性

- 递归扫描目录（含子目录）
- 文件监听 + 定时补扫（增量更新）
- PDF 与图片解析（文本提取 + OCR）
- 候选人信息抽取（姓名/电话/邮箱/学历/年限/技能）
- 文件名岗位解析（含多种命名模式）
- 可选 LLM 增强（阿里百炼 `qwen-doc-turbo`）
- SQLite 本地存储（按文件哈希去重）
- Web 查询（排序、分页、岗位下拉筛选、进度展示）
- 解析详情列（可隐藏/展示）
- 服务运维脚本（保活、重启、状态、清库重刷）

## 运行环境

- Python 3.11+
- macOS/Linux（Windows 需自行适配脚本）
- `tesseract`（OCR）

## 快速开始

```bash
cd local-resume-hub
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`（最少配置）：

- `RESUME_ROOT`：简历目录
- `DB_PATH`：SQLite 路径
- `HOST` / `PORT`：服务监听地址端口
- `DASHSCOPE_API_KEY`：可选，不填则禁用 LLM 增强

启动：

```bash
./scripts/run.sh
# 或
./scripts/service.sh start
```

访问：

- `http://127.0.0.1:8000/`（端口以 `.env` 为准）

## 运维命令

```bash
./scripts/service.sh start
./scripts/service.sh stop
./scripts/service.sh restart
./scripts/service.sh status
./scripts/service.sh health
./scripts/service.sh logs
./scripts/service.sh ensure-started
./scripts/service.sh reset-db
```

`reset-db` 会清空本地 SQLite 并触发全量重建索引。

## API

- `GET /api/health`
- `GET /api/progress`
- `GET /api/positions`
- `GET /api/candidates`
- `GET /api/candidates/{id}`
- `GET /api/candidates/{id}/file`（浏览器预览）
- `POST /api/rescan`

## 项目结构

```text
app/
  main.py        # FastAPI 路由与启动
  pipeline.py    # 文件监听、入库流水线、进度
  extractors.py  # OCR/文本解析与字段抽取
  llm.py         # LLM 增强调用
  db.py          # SQLite
scripts/
  run.sh
  service.sh
templates/
  index.html
tests/
```

## 隐私与安全

- 本项目设计为本地运行，不上传简历原文到远端（除非你启用 LLM 增强）。
- **不要提交**以下文件到公开仓库：
  - `.env`
  - `data/*.db`
  - `logs/`
  - `run/`
  - `.venv/`
- 发布前建议执行一次关键字扫描（API key/绝对路径/手机号等）。

## 测试

```bash
source .venv/bin/activate
pytest -q
```

## License

MIT
