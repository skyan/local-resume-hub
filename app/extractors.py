from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from pypdf import PdfReader
import pytesseract


try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None


SKILL_KEYWORDS = [
    "Python",
    "Java",
    "Go",
    "C++",
    "SQL",
    "MySQL",
    "PostgreSQL",
    "Redis",
    "Kafka",
    "Spark",
    "Flink",
    "TensorFlow",
    "PyTorch",
    "FastAPI",
    "Django",
    "Spring",
    "React",
    "Vue",
    "Kubernetes",
    "Docker",
]

POSITION_HINTS = [
    "开发工程师",
    "研发工程师",
    "工程师",
    "算法",
    "前端",
    "后端",
    "服务端",
    "大模型",
    "数据分析",
    "数据科学",
    "数据开发",
    "数据研发",
    "架构",
    "专家",
    "实习生",
    "研发",
    "开发",
]

POSITION_STOPWORDS = {
    "简历",
    "应届生",
    "毕业",
    "本科",
    "硕士",
    "博士",
    "大学",
    "学院",
    "手机用户",
}

COMPANY_PREFIXES = {
    "滴滴",
    "腾讯",
    "阿里",
    "百度",
    "字节",
    "美团",
    "京东",
    "快手",
    "网易",
}


@dataclass(slots=True)
class CandidateInfo:
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    education: str | None = None
    years_experience: str | None = None
    skills: str | None = None
    applied_position: str | None = None
    confidence: float = 0.0


def extract_text(path: Path) -> str:
    text, _ = extract_text_with_method(path)
    return text


def extract_text_with_method(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text_with_method(path)
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        return extract_image_text(path), "image_ocr"
    return "", "unknown"


def extract_pdf_text(path: Path) -> str:
    text, _ = extract_pdf_text_with_method(path)
    return text


def extract_pdf_text_with_method(path: Path) -> tuple[str, str]:
    chunks: list[str] = []
    try:
        reader = PdfReader(str(path))
        for page in reader.pages:
            chunks.append(page.extract_text() or "")
    except Exception:
        pass

    text = "\n".join(chunks).strip()
    if len(text) >= 80:
        return text, "pdf_text"

    if fitz is None:
        return text, "pdf_text"

    try:
        with fitz.open(path) as doc:
            ocr_chunks: list[str] = []
            page_limit = min(3, doc.page_count)
            for i in range(page_limit):
                page = doc.load_page(i)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                mode = "RGB" if pix.alpha == 0 else "RGBA"
                img = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
                ocr_chunks.append(pytesseract.image_to_string(img, lang="chi_sim+eng"))
            return "\n".join(ocr_chunks).strip(), "pdf_ocr"
    except Exception:
        return text, "pdf_text"


def extract_image_text(path: Path) -> str:
    try:
        img = Image.open(path)
        return pytesseract.image_to_string(img, lang="chi_sim+eng").strip()
    except Exception:
        return ""


def _filename_stem(file_name: str) -> str:
    return Path(file_name).stem.strip()


def extract_position_from_filename(file_name: str) -> str | None:
    name = _filename_stem(file_name)

    # Pattern: 【岗位_城市 薪资】姓名 ...
    m = re.search(r"^【([^_】]+)_", name)
    if m:
        return m.group(1).strip()

    # Pattern: 岗位-姓名【脉脉招聘】
    m = re.search(r"^(.+?)-[^-【】]{2,30}【[^】]+】$", name)
    if m:
        return m.group(1).strip()

    # Pattern: 岗位-姓名-工作X年-【脉脉招聘】
    m = re.search(r"^([^-【]+)-[^-]+-工作", name)
    if m:
        return m.group(1).strip()

    m = re.search(r"应聘(.+?)岗位", name)
    if m:
        return m.group(1).strip()

    # Pattern: 姓名-岗位 / 姓名-其他-岗位 / 公司前缀岗位-姓名-电话
    rough = _extract_position_by_hints(name)
    if rough:
        return rough

    return None


def extract_name_from_filename(file_name: str) -> str | None:
    name = _filename_stem(file_name)

    # Pattern: 【岗位_...】姓名 4年 / 26年应届生
    m = re.search(r"】\s*([\u4e00-\u9fa5A-Za-z·]{2,30})", name)
    if m:
        return m.group(1).strip()

    # Pattern: 岗位-姓名【脉脉招聘】
    m = re.search(r"-([\u4e00-\u9fa5A-Za-z·]{2,30})【", name)
    if m:
        return m.group(1).strip()

    # Pattern: 岗位-姓名-工作X年
    m = re.search(r"-([\u4e00-\u9fa5A-Za-z·]{2,30})-", name)
    if m:
        return m.group(1).strip()

    return None


def _extract_position_by_hints(name: str) -> str | None:
    # Normalize separators
    normalized = re.sub(r"[()（）\\[\\]【】]", " ", name)
    parts = [p.strip() for p in re.split(r"[-_\\s]+", normalized) if p.strip()]
    if not parts:
        return None

    candidates: list[str] = []
    for part in parts:
        if any(sw in part for sw in POSITION_STOPWORDS):
            continue
        if re.fullmatch(r"\\d{2,4}(?:年|应届生)?", part):
            continue
        if re.search(r"\\d{6,}", part):
            continue
        if any(h in part for h in POSITION_HINTS):
            candidates.append(part)

    if not candidates:
        return None

    # Prefer the most role-like text (longer and ending with common suffix)
    candidates.sort(key=lambda x: (x.endswith(("工程师", "专家", "实习生")), len(x)), reverse=True)
    best = candidates[0]

    # If a known company prefix exists (e.g., 滴滴前端), keep from first role hint.
    if any(best.startswith(prefix) for prefix in COMPANY_PREFIXES):
        idxs = [best.find(h) for h in POSITION_HINTS if h in best]
        if idxs:
            first_idx = min(i for i in idxs if i >= 0)
            if first_idx > 0 and len(best) - first_idx >= 2:
                best = best[first_idx:]

    best = best.strip("-_ ")
    return best or None


def extract_candidate_info(text: str, file_name: str) -> CandidateInfo:
    info = CandidateInfo()
    info.applied_position = extract_position_from_filename(file_name) or "未知岗位"

    email_m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    if email_m:
        info.email = email_m.group(0)

    phone_m = re.search(r"(?<!\d)(1[3-9]\d{9})(?!\d)", text)
    if phone_m:
        info.phone = phone_m.group(1)

    name_m = re.search(r"(?:姓名|Name)\s*[:：]\s*([\u4e00-\u9fa5A-Za-z·]{2,30})", text)
    if name_m:
        info.name = name_m.group(1).strip()
    else:
        info.name = extract_name_from_filename(file_name)

    edu_kw = ["博士", "硕士", "本科", "大专", "专科", "中专"]
    for kw in edu_kw:
        if kw in text:
            info.education = kw
            break

    year_m = re.search(r"(\d{1,2})\s*年(?:工作|经验)?", text)
    if year_m:
        info.years_experience = f"{year_m.group(1)}年"

    hits: list[str] = []
    t_low = text.lower()
    for kw in SKILL_KEYWORDS:
        if kw.lower() in t_low:
            hits.append(kw)
    if hits:
        info.skills = ", ".join(sorted(set(hits), key=hits.index))

    score = 0
    for field in [info.name, info.phone, info.email, info.education, info.years_experience, info.skills]:
        if field:
            score += 1
    info.confidence = score / 6
    return info


def merge_candidate_info(base: CandidateInfo, enhanced: dict) -> CandidateInfo:
    if not enhanced:
        return base

    def choose(current: str | None, key: str) -> str | None:
        value = enhanced.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return current

    base.name = choose(base.name, "name")
    base.phone = choose(base.phone, "phone")
    base.email = choose(base.email, "email")
    base.education = choose(base.education, "education")
    base.years_experience = choose(base.years_experience, "years_experience")
    base.skills = choose(base.skills, "skills")
    # Keep filename-derived position as source of truth unless it's missing/unknown.
    if base.applied_position in {None, "", "未知岗位"}:
        base.applied_position = choose(base.applied_position, "applied_position")

    score = 0
    for field in [base.name, base.phone, base.email, base.education, base.years_experience, base.skills]:
        if field:
            score += 1
    base.confidence = score / 6
    return base


def parse_json_from_text(raw: str) -> dict:
    try:
        return json.loads(raw)
    except Exception:
        pass

    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}
