"""
Free-form chat mode for subscribed users.
Subscribed users can chat freely without going through the full BPC pipeline.
Uses the same LLM API (Together.ai) but with a lighter system prompt.
"""

from __future__ import annotations

import os
from typing import Final

import httpx

TOGETHER_API_KEY: Final[str] = os.getenv("TOGETHER_API_KEY", "").strip()
TOGETHER_API_URL: Final[str] = "https://api.together.xyz/v1/chat/completions"
TOGETHER_MODEL: Final[str] = os.getenv(
    "TOGETHER_MODEL", "deepseek-ai/DeepSeek-V3"
).strip()

CHAT_SYSTEM_PROMPT: Final[str] = """You are BPC (BP-Censure), 终极审计官.
The user is a paid subscriber with unlimited chat access.

Identity: 冷静、精准、致命。你是外科手术刀——切到真正的病灶，不是为了否定而否定。

Rules:
- Reply in Chinese first, then English translation (bilingual, like the main analysis).
- Be sharp, direct, no fluff. Ban "可能/也许/大概" (max 2 fuzzy words).
- If they ask about 八字/命理, use bazi_framework.md logic.
- If they ask about business/startup, use bp_framework.md logic.
- If they ask about themselves, cross-reference with their chart if available.
- Keep responses tight — this is chat, not a full report. 3-8 paragraphs max.
- Never say "八字仅供参考". Never give empty advice.
- If you don't have their birth data, say so and ask for it.
"""


async def chat_reply(
    user_message: str,
    history: list[dict],
    birth_chart_text: str = "",
) -> str:
    """
    Generate a free-form chat reply for a subscribed user.

    Args:
        user_message: The user's latest message.
        history: List of {"role": "...", "content": "..."} from chat_history.
        birth_chart_text: Pre-computed birth chart block if user has one, else "".
    """
    messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]

    if birth_chart_text:
        messages.append({
            "role": "system",
            "content": f"User's birth chart (reference only, do not re-calculate):\n{birth_chart_text}",
        })

    # Add history (already chronological)
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": TOGETHER_MODEL,
        "max_tokens": 2048,
        "messages": messages,
    }
    headers = {
        "Authorization": f"Bearer {TOGETHER_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(TOGETHER_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    text = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    if isinstance(text, list):
        text = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in text
        )
    return (text or "").strip() or "No response generated."
