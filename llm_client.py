"""
Unified LLM client for BPC.
Supports two providers, selected by LLM_PROVIDER env var:
  - "together" (default): DeepSeek-V3 via Together.ai
  - "glm": Zhipu BigModel (OpenAI-compatible)

Both return plain text. Callers don't need to know which provider is live.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

# ── Provider selection ──────────────────────────────────────────────────────
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "together").strip().lower()

# Together.ai
TOGETHER_API_KEY: str = os.getenv("TOGETHER_API_KEY", "").strip()
TOGETHER_API_URL: str = "https://api.together.xyz/v1/chat/completions"
TOGETHER_MODEL: str = os.getenv("TOGETHER_MODEL", "deepseek-ai/DeepSeek-V3").strip()

# Zhipu GLM (BigModel) — OpenAI-compatible
GLM_API_KEY: str = os.getenv("GLM_API_KEY", "").strip()
GLM_API_URL: str = os.getenv(
    "GLM_API_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions"
).strip()
GLM_MODEL: str = os.getenv("GLM_MODEL", "glm-4.5-flash").strip()


def _active_config() -> tuple[str, str, str]:
    """Return (url, api_key, model) for the active provider."""
    if LLM_PROVIDER == "glm":
        return GLM_API_URL, GLM_API_KEY, GLM_MODEL
    return TOGETHER_API_URL, TOGETHER_API_KEY, TOGETHER_MODEL


async def chat_completion(
    messages: list[dict[str, Any]],
    max_tokens: int = 4096,
    timeout: float = 120.0,
    temperature: float | None = None,
) -> str:
    """
    Send a chat completion request to the active provider and return text.

    Args:
        messages: OpenAI-style message list.
        max_tokens: maximum output tokens.
        timeout: request timeout in seconds.
        temperature: optional sampling temperature.
    """
    url, api_key, model = _active_config()

    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if temperature is not None:
        payload["temperature"] = temperature

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    text = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    if isinstance(text, list):
        # Some providers return content parts as a list
        text = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in text
        )
    return (text or "").strip()


def active_model_name() -> str:
    """Name of the model currently in use (for logging)."""
    _, _, model = _active_config()
    return f"{LLM_PROVIDER}:{model}"
