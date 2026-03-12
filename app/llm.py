from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx


logger = logging.getLogger(__name__)


class LLMEnhancer:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

    def enabled(self) -> bool:
        return bool(self.api_key and self.model)

    async def enhance(self, text: str, filename: str) -> dict[str, Any]:
        data, _ = await self.enhance_with_meta(text=text, filename=filename)
        return data

    async def enhance_with_meta(self, text: str, filename: str) -> tuple[dict[str, Any], str | None]:
        if not self.enabled() or not text.strip():
            logger.info(
                "llm.skip filename=%s reason=disabled_or_empty_text enabled=%s text_len=%d",
                filename,
                self.enabled(),
                len(text or ""),
            )
            return {}, "disabled_or_empty_text"

        prompt = (
            "你是简历结构化助手。请从简历文本和文件名中抽取字段，"
            "仅返回JSON，不要额外解释。字段: "
            "name, phone, email, education, years_experience, skills, applied_position。"
            "若字段缺失请返回空字符串。"
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": f"文件名: {filename}\n简历文本:\n{text[:12000]}",
                },
            ],
            "temperature": 0,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=40) as client:
                resp = await client.post(self.base_url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
                logger.info(
                    "llm.http_ok filename=%s status=%s text_len=%d response_len=%d",
                    filename,
                    resp.status_code,
                    len(text),
                    len(content),
                )
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", "n/a")
            body = ""
            try:
                body = getattr(e, "response", None).text[:300]  # type: ignore[union-attr]
            except Exception:
                body = ""
            logger.warning(
                "llm.http_error filename=%s status=%s err=%s body=%s",
                filename,
                status,
                type(e).__name__,
                _sanitize(body),
            )
            return {}, f"http_error:{type(e).__name__}"

        try:
            parsed, parse_err = _parse_json_content(content)
            if parsed:
                logger.info("llm.json_ok filename=%s keys=%s", filename, ",".join(sorted(parsed.keys())))
                return parsed, None
            logger.warning(
                "llm.json_parse_failed filename=%s reason=%s content_preview=%s",
                filename,
                parse_err,
                _sanitize(content),
            )
            return {}, parse_err or "json_parse_failed"
        except Exception:
            logger.warning("llm.json_parse_failed filename=%s reason=unexpected", filename)
            return {}, "json_parse_failed"


def _sanitize(value: str) -> str:
    if not value:
        return ""
    one_line = " ".join(value.split())
    if len(one_line) > 300:
        return one_line[:300] + "...(truncated)"
    return one_line


def _parse_json_content(content: str) -> tuple[dict[str, Any], str | None]:
    raw = (content or "").strip()
    if not raw:
        return {}, "json_empty"

    # Handle fenced markdown blocks: ```json ... ```
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, flags=re.IGNORECASE)
    if fenced:
        raw = fenced.group(1).strip()

    # First attempt: direct parse
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed, None
        return {}, "non_dict_response"
    except Exception:
        pass

    # Second attempt: extract the first JSON object block
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = raw[start : end + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed, None
            return {}, "non_dict_response"
        except Exception:
            return {}, "json_parse_failed"

    return {}, "json_object_not_found"
