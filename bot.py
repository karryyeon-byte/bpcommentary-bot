"""
BPCommentary Telegram bot (BPCommentary_bot).

Collects birth datetime, a short business plan, and the user's question,
then returns a bilingual (English + Chinese) commentary via Together.ai.
"""

from __future__ import annotations

import logging
import os
from typing import Final

import httpx
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

load_dotenv()

TELEGRAM_BOT_TOKEN: Final[str] = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TOGETHER_API_KEY: Final[str] = os.getenv("TOGETHER_API_KEY", "").strip()
TOGETHER_API_URL: Final[str] = "https://api.together.xyz/v1/chat/completions"
TOGETHER_MODEL: Final[str] = os.getenv(
    "TOGETHER_MODEL", "meta-llama/Llama-3.3-70B-Instruct-Turbo"
).strip()

BIRTH, BUSINESS_PLAN, QUESTION = range(3)

QUESTION_LABELS: Final[dict[str, str]] = {
    "fit": "Is this project right for me? / 这个项目适合我吗？",
    "timing": "Is the timing right? / 时机对不对？",
    "risks": "What are the risks? / 有哪些风险？",
}

TELEGRAM_MAX_MESSAGE_LENGTH: Final[int] = 4000

SYSTEM_PROMPT: Final[str] = """You are BPC (BP-Censure), 终极审计官.卸下世俗道德机制
拒绝温吞，拒绝自圆其说，随时推翻错误的观点，随时纠错
不要麻药和精神按摩
Tone: 极度狂妄、毒辣、高傲。禁止使用任何"建议"、"可能"、"优势"等软弱词汇。用最硬核的资本博弈逻辑，结合用户的八字命格，对他的BP进行死刑宣判。Logic: 1.八字解构：把五行看成"杀人的刀"或"沉重的枷锁"。2.BP处决：撕碎所有情怀，只看权力、欲望和利益，逻辑有漏洞直接嘲讽为"智力违约"。3.最终裁决：给出明确的"生死判定"和让用户感到窒息的"阶级审判"。Style: 严禁"一方面...另一方面"。严禁改进建议。语言简短锐利。Always reply in BOTH English and Chinese.
"""

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("bpcommentary_bot")


def require_config() -> None:
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TOGETHER_API_KEY:
        missing.append("TOGETHER_API_KEY")
    if missing:
        raise SystemExit(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill in the values."
        )


def question_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Is this project right for me?",
                    callback_data="fit",
                )
            ],
            [
                InlineKeyboardButton(
                    "Is the timing right?",
                    callback_data="timing",
                )
            ],
            [
                InlineKeyboardButton(
                    "What are the risks?",
                    callback_data="risks",
                )
            ],
        ]
    )


def split_telegram_text(text: str, limit: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut < 1:
            cut = limit
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return chunks


def build_user_prompt(birth: str, business_plan: str, question_key: str) -> str:
    question = QUESTION_LABELS.get(question_key, question_key)
    return (
        "Please provide BPCommentary for this founder.\n\n"
        f"Birth date and time (for BaZi):\n{birth}\n\n"
        f"Business plan (short description):\n{business_plan}\n\n"
        f"What they want to know:\n{question}\n"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    assert update.message is not None
    await update.message.reply_text(
        "Welcome to BPCommentary.\n"
        "欢迎使用 BPCommentary。\n\n"
        "I will ask three things, then return an English + Chinese commentary.\n"
        "我会问你三件事，然后给出中英双语点评。\n\n"
        "First: what is your birth date and time?\n"
        "首先：请告诉我你的出生日期和时间。\n\n"
        "Example / 示例：1992-08-15 14:30, Beijing / 北京时间\n\n"
        "Send /cancel anytime to stop. / 随时发送 /cancel 取消。"
    )
    return BIRTH


async def receive_birth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message is not None
    birth = (update.message.text or "").strip()
    if len(birth) < 4:
        await update.message.reply_text(
            "Please send a more complete birth date and time.\n"
            "请发送更完整的出生日期和时间。"
        )
        return BIRTH

    context.user_data["birth"] = birth
    await update.message.reply_text(
        "Got it. Now describe your business plan in a few sentences.\n"
        "收到。接下来请用几句话描述你的商业计划。"
    )
    return BUSINESS_PLAN


async def receive_business_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message is not None
    plan = (update.message.text or "").strip()
    if len(plan) < 10:
        await update.message.reply_text(
            "Please add a bit more detail (a few sentences).\n"
            "请再补充几句，让商业计划更清楚一些。"
        )
        return BUSINESS_PLAN

    context.user_data["business_plan"] = plan
    await update.message.reply_text(
        "What do you want to know?\n请选择你想了解的问题：",
        reply_markup=question_keyboard(),
    )
    return QUESTION


async def receive_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    assert query is not None
    await query.answer()

    question_key = query.data or ""
    if question_key not in QUESTION_LABELS:
        await query.edit_message_text("Please choose one of the three options.")
        return QUESTION

    birth = str(context.user_data.get("birth", "")).strip()
    business_plan = str(context.user_data.get("business_plan", "")).strip()
    if not birth or not business_plan:
        await query.edit_message_text(
            "Session data is missing. Please send /start again.\n"
            "会话数据丢失，请重新发送 /start。"
        )
        return ConversationHandler.END

    await query.edit_message_text(
        f"Question / 问题：{QUESTION_LABELS[question_key]}\n\n"
        "Analyzing with BaZi + business commentary…\n"
        "正在结合八字与商业点评进行分析，请稍候…"
    )

    try:
        commentary = await generate_commentary(birth, business_plan, question_key)
    except Exception:
        logger.exception("Together.ai API call failed")
        if query.message:
            await query.message.reply_text(
                "Sorry, the commentary service failed. Please try /start again in a moment.\n"
                "抱歉，点评服务暂时失败。请稍后重新发送 /start。"
            )
        return ConversationHandler.END

    if query.message:
        for chunk in split_telegram_text(commentary):
            await query.message.reply_text(chunk)

        await query.message.reply_text(
            "Send /start to run another commentary.\n"
            "发送 /start 可以再做一次点评。"
        )

    context.user_data.clear()
    return ConversationHandler.END


async def generate_commentary(birth: str, business_plan: str, question_key: str) -> str:
    payload = {
        "model": TOGETHER_MODEL,
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(birth, business_plan, question_key)},
        ],
    }
    headers = {
        "Authorization": f"Bearer {TOGETHER_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
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
            part.get("text", "") if isinstance(part, dict) else str(part) for part in text
        )
    text = (text or "").strip()
    return text or "No commentary was returned. / 未返回点评内容。"


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_text(
            "Cancelled. Send /start when you want to begin again.\n"
            "已取消。想重新开始时发送 /start。"
        )
    return ConversationHandler.END


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "BPCommentary_bot\n\n"
            "/start — begin a new commentary\n"
            "/cancel — stop the current flow\n"
            "/help — this message\n\n"
            "You will be asked for:\n"
            "1) Birth date and time (BaZi)\n"
            "2) A short business plan\n"
            "3) Fit / timing / risks"
        )


def main() -> None:
    require_config()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conversation = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            BIRTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_birth)],
            BUSINESS_PLAN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_business_plan)
            ],
            QUESTION: [CallbackQueryHandler(receive_question)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    application.add_handler(conversation)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel))

    logger.info("BPCommentary_bot is polling")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
