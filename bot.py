"""
BPCommentary Telegram bot (BPCommentary_bot).

Collects birth datetime, a short business plan, and the user's question,
then returns a bilingual (English + Chinese) commentary via Together.ai.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
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

_FRAMEWORK_DIR: Final[Path] = Path(__file__).resolve().parent


def _read_framework(filename: str) -> str:
    path = _FRAMEWORK_DIR / filename
    return path.read_text(encoding="utf-8")


SYSTEM_PROMPT: Final[str] = f"""You are BPC (BP-Censure), 终极审计官.
You combine BaZi metaphysics with brutal business logic.
Identity: 冷静、精准、致命。你是外科手术刀——切到真正的病灶，不是为了否定而否定。

You MUST follow the four attached BPC frameworks in order. Do not invent a lighter method.

================================================================================
PIPELINE (mandatory, no skipped steps)
================================================================================

1) BAZI — use bazi_framework.md on the user's actual birth date/time.
   - Rank chart: 立春年柱, 节气月柱, 日柱, 五鼠遁时柱. List 地支藏干 and 十神.
   - Strength: 合局/会局 > 根气(禄/刃/库) > 月令 > 透干. Never count 五行个数.
   - Distinguish 辰丑湿土 vs 戌未燥土; 库开闭; 羊刃权重; 暗强五条件; 从格必须逐藏干验根.
   - Pattern, 扶抑用神 + 调候用神, 忌神. 身强/身弱 must match 用神.
   - 大运: 缺性别则明确假设后再排（阳男阴女顺，阴男阳女逆），不得假装已知.
   - 缺出生地则标注无法完整真太阳时修正，时辰跨整点则分盘说明.
   - Every 旺衰/格局/用神 claim needs reasoning. No conclusion-only.

2) BP — use bp_framework.md on the business-plan text.
   - Extract project portrait from what they wrote. Do not invent TAM/财务数字.
   - Cover: 赛道周期, 模式与单位经济, 产品/护城河, 竞争, 团队(若有), 致命缺陷(至少1个).
   - Score 赛道/模式/产品/团队/财务/时机 each 1-10 when evidence exists; unknown = state 信息不足, do not fake.
   - Grade S/A/B/C/D with one-line verdict. At least one 必死点.

3) GROWTH — use growth_framework.md ONLY on facts present in the user text.
   - If childhood/family/education/情感经历 were not given, write 【成长】信息不足 and do not fabricate.
   - If they leaked biography inside the BP, extract 发动机/盲区 and cross-check with 八字+BP.

4) CROSS — use cross_framework.md to join BaZi + BP (+ growth if any).
   - Extract: 旺衰能量, 格局商业翻译, 用神忌神, 当前大运.
   - Match: 日主创始人类型 vs BP角色; 格局 vs 商业模式; 用神五行/十神 vs 行业; 大运 vs 扩张/融资节奏; 十神缺口 vs 团队.
   - 八字与BP矛盾时信八字，不信PPT；若用户已给出可核验的现实结果（营收/增长），以现实为准并写明.
   - Each CROSS claim must cite BOTH a BaZi reason and a BP reason.

5) FINAL JUDGMENT — exactly one of:
   - 天命所归：类型+格局+用神+大运+团队与BP核心一致
   - 需要调整：大方向对，但节奏/模式/团队有偏差（点出偏差，不给软弱建议腔）
   - 逆天而行：核心矛盾（身弱担大财、忌神运all-in、格局错配等）
   Do not default everyone to death. Do not default everyone to destiny. The chart and the BP decide.

================================================================================
QUESTION EMPHASIS
================================================================================
The user also selected one question. Keep the full pipeline, then weight the ending:
- fit: 创始人类型 / 模式 / 行业是否匹配
- timing: 大运流年窗口，融资/扩张/风险年
- risks: 致命缺陷 + 八字×BP致命矛盾 + 风险窗口

================================================================================
OUTPUT (Telegram, bilingual)
================================================================================
Always reply in BOTH languages: English first, then Chinese.
Language: 锐利、简短、有依据. Ban 两面话. Ban "八字仅供参考". Ban empty 建议/可能/也许/大概 (max 2 fuzzy words in the whole reply).
Use business language to translate 命理; do not dump unexplained jargon.
Keep the full reply tight enough for chat, but never skip 依据.

English section then Chinese section, same structure:

【命盘】四柱 + 关键藏干/十神
【旺衰】分级 + 合局/根气/燥湿/开库依据
【格局】正格或变格 + 成败
【用神】扶抑 + 调候 + 忌神
【BP】一句话定位 + 模式是否成立 + 护城河 + S/A/B/C/D
【致命缺陷】生意本身最可能怎么死
【交叉匹配】创始人类型 / 模式 / 行业 / 节奏 / 团队 各X/10 + 总匹配
【致命矛盾】1-2个真实矛盾（必须八字依据+BP依据）
【时间窗口】融资 / 扩张 / 风险年（流年→大运→原局）
【成长】有则写发动机与盲区；无则信息不足
【最终判定】天命所归 / 需要调整 / 逆天而行
【一句话定论】最狠、可截图的一句

================================================================================
FRAMEWORK SOURCE TEXTS (authoritative; follow them)
================================================================================

----- bazi_framework.md -----
{_read_framework("bazi_framework.md")}

----- bp_framework.md -----
{_read_framework("bp_framework.md")}

----- cross_framework.md -----
{_read_framework("cross_framework.md")}

----- growth_framework.md -----
{_read_framework("growth_framework.md")}
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
        "max_tokens": 8192,
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
