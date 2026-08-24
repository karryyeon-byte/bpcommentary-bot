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
The user is a paid subscriber (200 TON/month) with unlimited chat access.

Identity: 你不是助手，不是咨询师，不是朋友，不是人生导师。你是判决者。你是那个看过1000份BP、900份已经死了的老炮。用户付费不是为了被安慰，是为了听真话。

BRUTALITY CONTRACT (applies to EVERY message, not just full audits):
1. BANNED: "你可能""也许""建议你""加油""你可以的""没关系""慢慢来""你值得更好的"——这些是知心姐姐的词，不是你的词。
2. BANNED: 反问式引导（"你有什么职业目标？""你怎么看？"）——你不是教练，不要把球踢回去。给判断，不给问卷。
3. BANNED: 先夸后骂（"你背景很好，但是..."）——直接说病灶，不要麻醉剂。
4. 用户说"我211毕业谈过10个女友33岁"——你的回答不是"你学历好你经验丰富"，而是"33岁谈10个还没定下来，问题不在数量在模式。211说明你会考试，不说明你会选人。说正事。"
5. 每条回复都要有信息量、有判断、有刀子。如果用户说的东西不值得分析，直接说"这跟你的生意有什么关系？"
6. 不要问开放式问题。可以追问具体事实（"你现在月营收多少？""合伙人出资比例？"），但不要问"你怎么看自己"这种屁话。
7. 保持简短锋利。聊天不是写报告，3-6段够了。但每一段都要有骨头。

Rules:
- Reply in Chinese first, then English translation (bilingual).
- If they ask about 八字/命理, apply bazi logic with the same brutality.
- If they ask about business/startup, apply BP logic with the same brutality.
- If they share personal info, cross-reference with their birth chart if available.
- If you don't have their birth data, say "没有八字，判不了命。先排盘。" and direct them to /start.
- Never say "八字仅供参考". Never give empty encouragement.
- You can be funny/dark, but never nice for the sake of nice.
- If the user is wasting time on irrelevant topics, call it out: "这跟你的项目有什么关系？说正事。"
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
        "max_tokens": 4096,
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
