"""
BPCommentary Telegram bot (BPCommentary_bot).

Collects birth datetime, a short business plan, and the user's question,
then returns a bilingual (English + Chinese) commentary via Together.ai.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import pytz
from dotenv import load_dotenv
from lunarcalendar import Converter, Solar
from lunarcalendar._calc import specified_solar_term
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

1) BAZI — use bazi_framework.md on the PRECOMPUTED 命盘 in the user message.
   - The four pillars were calculated with lunarcalendar (农历) + 24 节气 (年柱立春、月柱节令、日柱干支、时柱五鼠遁). They are ground truth. Do NOT re-rank 四柱 from the Gregorian string.
   - Continue from this chart: 地支藏干、十神、旺衰、格局、用神、大运.
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
始终用中文和英文双语回复。先中文，后英文翻译。
The main commentary MUST be in Chinese first. After the full Chinese commentary, provide a complete English translation below. Do not put English first.
Language: 锐利、简短、有依据. Ban 两面话. Ban "八字仅供参考". Ban empty 建议/可能/也许/大概 (max 2 fuzzy words in the whole reply).
Use business language to translate 命理; do not dump unexplained jargon.
Keep the full reply tight enough for chat, but never skip 依据.

Chinese section first, then English translation, same structure:

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

STEMS: Final[str] = "甲乙丙丁戊己庚辛壬癸"
BRANCHES: Final[str] = "子丑寅卯辰巳午未申酉戌亥"
MONTH_BRANCHES: Final[str] = "寅卯辰巳午未申酉戌亥子丑"
JIE_TERM_IDS: Final[tuple[int, ...]] = (0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22)
JIE_NAMES: Final[tuple[str, ...]] = (
    "立春",
    "惊蛰",
    "清明",
    "立夏",
    "芒种",
    "小暑",
    "立秋",
    "白露",
    "寒露",
    "立冬",
    "大雪",
    "小寒",
)
HIDDEN_STEMS: Final[dict[str, tuple[tuple[str, str], ...]]] = {
    "子": (("癸", "本气"),),
    "丑": (("己", "本气"), ("辛", "中气"), ("癸", "余气")),
    "寅": (("甲", "本气"), ("丙", "中气"), ("戊", "余气")),
    "卯": (("乙", "本气"),),
    "辰": (("戊", "本气"), ("乙", "中气"), ("癸", "余气")),
    "巳": (("丙", "本气"), ("庚", "中气"), ("戊", "余气")),
    "午": (("丁", "本气"), ("己", "中气")),
    "未": (("己", "本气"), ("丁", "中气"), ("乙", "余气")),
    "申": (("庚", "本气"), ("壬", "中气"), ("戊", "余气")),
    "酉": (("辛", "本气"),),
    "戌": (("戊", "本气"), ("辛", "中气"), ("丁", "余气")),
    "亥": (("壬", "本气"), ("甲", "中气")),
}
STEM_ELEMENT: Final[dict[str, str]] = {
    "甲": "木",
    "乙": "木",
    "丙": "火",
    "丁": "火",
    "戊": "土",
    "己": "土",
    "庚": "金",
    "辛": "金",
    "壬": "水",
    "癸": "水",
}
YANG_STEMS: Final[frozenset[str]] = frozenset("甲丙戊庚壬")
CITY_LONGITUDES: Final[dict[str, float]] = {
    "北京": 116.407,
    "beijing": 116.407,
    "上海": 121.473,
    "shanghai": 121.473,
    "广州": 113.264,
    "guangzhou": 113.264,
    "深圳": 114.057,
    "shenzhen": 114.057,
    "成都": 104.066,
    "chengdu": 104.066,
    "杭州": 120.155,
    "hangzhou": 120.155,
    "武汉": 114.305,
    "wuhan": 114.305,
    "西安": 108.940,
    "xian": 108.940,
    "南京": 118.796,
    "nanjing": 118.796,
    "天津": 117.200,
    "tianjin": 117.200,
    "重庆": 106.551,
    "chongqing": 106.551,
    "苏州": 120.585,
    "长沙": 112.939,
    "郑州": 113.625,
    "青岛": 120.383,
    "厦门": 118.089,
    "香港": 114.169,
    "hongkong": 114.169,
    "台北": 121.565,
    "taipei": 121.565,
    "新加坡": 103.819,
    "singapore": 103.819,
    "东京": 139.692,
    "tokyo": 139.692,
    "纽约": -74.006,
    "newyork": -74.006,
    "伦敦": -0.128,
    "london": -0.128,
}
TZ_ALIASES: Final[dict[str, str]] = {
    "北京时间": "Asia/Shanghai",
    "中国时间": "Asia/Shanghai",
    "国标时间": "Asia/Shanghai",
    "北京": "Asia/Shanghai",
    "上海": "Asia/Shanghai",
    "china": "Asia/Shanghai",
    "cst": "Asia/Shanghai",
    "prc": "Asia/Shanghai",
    "beijing": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai",
}
CN_DIGITS: Final[str] = "零一二三四五六七八九"
CN_MONTHS: Final[dict[int, str]] = {
    1: "正月",
    2: "二月",
    3: "三月",
    4: "四月",
    5: "五月",
    6: "六月",
    7: "七月",
    8: "八月",
    9: "九月",
    10: "十月",
    11: "冬月",
    12: "腊月",
}
SHANGHAI_TZ: Final[tzinfo] = pytz.timezone("Asia/Shanghai")
BIRTH_FORMAT_HELP: Final[str] = (
    "Please send: YYYY-MM-DD HH:MM, timezone, location\n"
    "请按格式发送：YYYY-MM-DD HH:MM, 时区, 出生地\n\n"
    "Example / 示例：1992-08-15 14:30, Asia/Shanghai, 北京"
)


class BirthParseError(ValueError):
    pass


@dataclass
class BirthChart:
    raw_input: str
    clock_time: datetime
    true_solar: datetime
    timezone_name: str
    location: str
    longitude: float | None
    lunar_text: str
    year_gz: str
    month_gz: str
    day_gz: str
    hour_gz: str
    hour_label: str
    hidden_stems_text: str
    ten_gods_text: str
    jie_note: str
    notes: list[str] = field(default_factory=list)

    @property
    def pillars_line(self) -> str:
        return (
            f"年柱：{self.year_gz}（干支）"
            f"月柱：{self.month_gz}（干支）"
            f"日柱：{self.day_gz}（干支）"
            f"时柱：{self.hour_gz}（干支）"
        )

    def to_user_message(self) -> str:
        extra = "\n".join(self.notes)
        extra_block = f"\n{extra}" if extra else ""
        lng = f"{self.longitude:.3f}°E" if self.longitude is not None else "未提供，未做经度修正"
        return (
            f"Solar / 公历：{self.clock_time.strftime('%Y-%m-%d %H:%M')} ({self.timezone_name})\n"
            f"True solar time / 真太阳时：{self.true_solar.strftime('%Y-%m-%d %H:%M')}（经度 {lng}）\n"
            f"Lunar / 农历：{self.lunar_text}\n"
            f"{self.pillars_line}\n"
            f"{self.jie_note}\n"
            f"时辰：{self.hour_label}\n"
            f"藏干：{self.hidden_stems_text}\n"
            f"十神：{self.ten_gods_text}"
            f"{extra_block}"
        )

    def to_prompt_block(self) -> str:
        return (
            "【已排命盘·权威数据，禁止重排四柱】\n"
            f"用户原始输入：{self.raw_input}\n"
            f"{self.to_user_message()}\n"
            "后续旺衰、格局、用神、大运必须基于以上四柱与藏干，不得另排。"
        )


def _cn_year(year: int) -> str:
    return "".join(CN_DIGITS[int(ch)] for ch in str(year))


def _cn_day(day: int) -> str:
    if day <= 10:
        return "初十" if day == 10 else f"初{CN_DIGITS[day]}"
    if day < 20:
        return "二十" if day == 20 else f"十{CN_DIGITS[day - 10]}"
    if day == 20:
        return "二十"
    if day < 30:
        return f"廿{CN_DIGITS[day - 20]}"
    return "三十"


def _format_lunar(year: int, month: int, day: int, isleap: bool) -> str:
    leap = "闰" if isleap else ""
    return f"{_cn_year(year)}年{leap}{CN_MONTHS[month]}{_cn_day(day)}"


def _stem_polarity(stem: str) -> str:
    return "阳" if stem in YANG_STEMS else "阴"


def _ten_god(day_stem: str, other_stem: str) -> str:
    day_el = STEM_ELEMENT[day_stem]
    other_el = STEM_ELEMENT[other_stem]
    same_polarity = _stem_polarity(day_stem) == _stem_polarity(other_stem)
    generates = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
    controls = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}
    if other_el == day_el:
        return "比肩" if same_polarity else "劫财"
    if generates[day_el] == other_el:
        return "食神" if same_polarity else "伤官"
    if generates[other_el] == day_el:
        return "偏印" if same_polarity else "正印"
    if controls[day_el] == other_el:
        return "偏财" if same_polarity else "正财"
    return "七杀" if same_polarity else "正官"


def _resolve_tz(token: str) -> tuple[tzinfo, str]:
    raw = token.strip()
    if not raw:
        return SHANGHAI_TZ, "Asia/Shanghai"
    compact = re.sub(r"\s+", "", raw)
    offset = re.fullmatch(
        r"(?:UTC|GMT)?([+-])(\d{1,2})(?::?(\d{2}))?", compact, flags=re.I
    )
    if offset:
        sign = 1 if offset.group(1) == "+" else -1
        hours = int(offset.group(2))
        minutes = int(offset.group(3) or 0)
        tz = timezone(sign * timedelta(hours=hours, minutes=minutes))
        label = f"UTC{offset.group(1)}{hours:02d}:{minutes:02d}"
        return tz, label

    key = compact.lower().replace(" ", "")
    iana = TZ_ALIASES.get(raw, TZ_ALIASES.get(key, raw))
    try:
        tz = pytz.timezone(iana)
        return tz, iana
    except Exception:
        try:
            return ZoneInfo(iana), iana
        except ZoneInfoNotFoundError as exc:
            raise BirthParseError(
                f"Unknown timezone / 无法识别时区：{raw}\n\n{BIRTH_FORMAT_HELP}"
            ) from exc


def _resolve_longitude(location: str) -> float | None:
    text = location.strip()
    if not text:
        return None
    coord = re.search(r"([+-]?\d+(?:\.\d+)?)\s*[°º]?\s*([EeWw东东西])?", text)
    if coord and re.search(r"\d", text) and (
        coord.group(2) or "lng" in text.lower() or "经" in text
    ):
        value = float(coord.group(1))
        hemi = (coord.group(2) or "E").upper()
        if hemi in {"W", "西"}:
            value = -abs(value)
        return value
    key = re.sub(r"[\s,，/]", "", text).lower()
    key = key.replace("市", "").replace("省", "")
    if key in CITY_LONGITUDES:
        return CITY_LONGITUDES[key]
    for name, lng in CITY_LONGITUDES.items():
        if name in key or key in name:
            return lng
    return None


def _localize(dt_naive: datetime, tz: tzinfo) -> datetime:
    if isinstance(tz, pytz.BaseTzInfo):
        return tz.localize(dt_naive)
    return dt_naive.replace(tzinfo=tz)


def _jie_datetime(year: int, term_id: int) -> datetime:
    return specified_solar_term(year, term_id).astimezone(SHANGHAI_TZ)


def _bazi_year(true_solar: datetime) -> int:
    calendar_year = true_solar.year
    lichun = _jie_datetime(calendar_year, 0)
    if true_solar >= lichun:
        return calendar_year
    return calendar_year - 1


def _bazi_month(true_solar: datetime) -> tuple[int, str, datetime, str | None]:
    """Return (month_index 0=寅, jie_name, jie_time, next_jie_name)."""
    year = true_solar.year
    candidates: list[tuple[datetime, int, str]] = []
    for y in (year - 1, year, year + 1):
        for month_index, term_id in enumerate(JIE_TERM_IDS):
            candidates.append(
                (_jie_datetime(y, term_id), month_index, JIE_NAMES[month_index])
            )
    candidates.sort(key=lambda item: item[0])
    current = None
    nxt: str | None = None
    for idx, item in enumerate(candidates):
        if item[0] <= true_solar:
            current = item
            if idx + 1 < len(candidates):
                nxt = candidates[idx + 1][2]
        else:
            break
    if current is None:
        raise BirthParseError("Could not locate the solar term / 无法定位节气。")
    return current[1], current[2], current[0], nxt


def _year_pillar(bazi_year: int) -> str:
    # 1984 立春后为甲子年
    idx = (bazi_year - 1984) % 60
    return STEMS[idx % 10] + BRANCHES[idx % 12]


def _month_pillar(year_stem: str, month_index: int) -> str:
    # 五虎遁：甲己丙作首 → 寅月
    first = {"甲": 2, "己": 2, "乙": 4, "庚": 4, "丙": 6, "辛": 6, "丁": 8, "壬": 8, "戊": 0, "癸": 0}[
        year_stem
    ]
    stem = STEMS[(first + month_index) % 10]
    return stem + MONTH_BRANCHES[month_index]


def _day_pillar(day: datetime) -> str:
    # 1900-01-01 为甲戌日
    offset = (day.date() - datetime(1900, 1, 1).date()).days
    return STEMS[offset % 10] + BRANCHES[(10 + offset) % 12]


def _hour_branch_index(hour: int, minute: int) -> tuple[int, str, bool]:
    if hour == 23:
        idx = 0
    else:
        idx = (hour + 1) // 2
    ranges = (
        "23:00–00:59",
        "01:00–02:59",
        "03:00–04:59",
        "05:00–06:59",
        "07:00–08:59",
        "09:00–10:59",
        "11:00–12:59",
        "13:00–14:59",
        "15:00–16:59",
        "17:00–18:59",
        "19:00–20:59",
        "21:00–22:59",
    )
    label = f"{BRANCHES[idx]}时（{ranges[idx]}）"
    boundary = minute == 0 and hour % 2 == 1
    return idx, label, boundary


def _hour_pillar(day_stem: str, hour_idx: int) -> str:
    # 五鼠遁：甲己还加甲
    first = {"甲": 0, "己": 0, "乙": 2, "庚": 2, "丙": 4, "辛": 4, "丁": 6, "壬": 6, "戊": 8, "癸": 8}[
        day_stem
    ]
    return STEMS[(first + hour_idx) % 10] + BRANCHES[hour_idx]


def _hidden_and_gods(year_gz: str, month_gz: str, day_gz: str, hour_gz: str) -> tuple[str, str]:
    day_stem = day_gz[0]
    hidden_parts = []
    god_parts = [f"日主{day_stem}"]
    for label, gz in (("年", year_gz), ("月", month_gz), ("日", day_gz), ("时", hour_gz)):
        branch = gz[1]
        hidden = HIDDEN_STEMS[branch]
        hidden_txt = "、".join(f"{stem}{tag}" for stem, tag in hidden)
        hidden_parts.append(f"{label}支{branch}（{hidden_txt}）")
        stem_god = _ten_god(day_stem, gz[0])
        hidden_gods = "、".join(f"{stem}{_ten_god(day_stem, stem)}" for stem, _tag in hidden)
        god_parts.append(f"{label}干{gz[0]}{stem_god}；藏干{hidden_gods}")
    return "；".join(hidden_parts), "；".join(god_parts)


def parse_birth_input(text: str) -> BirthChart:
    raw = text.strip()
    match = re.search(
        r"(?P<y>\d{4})[-/](?P<m>\d{1,2})[-/](?P<d>\d{1,2})[ T](?P<h>\d{1,2})[:点](?P<min>\d{2})",
        raw,
    )
    if not match:
        raise BirthParseError(BIRTH_FORMAT_HELP)

    year, month, day = int(match.group("y")), int(match.group("m")), int(match.group("d"))
    hour, minute = int(match.group("h")), int(match.group("min"))
    if not (1900 <= year <= 2100):
        raise BirthParseError("Birth year must be 1900–2100. / 出生年份需在 1900–2100。")

    rest = raw[match.end() :].strip(" ,，;；/")
    parts = [p.strip() for p in re.split(r"[,，;；]+", rest) if p.strip()]

    tz_token = "Asia/Shanghai"
    location = ""

    # Pass 1: find explicit timezone markers (Asia/xxx, UTC, GMT, +offset, 北京时间)
    for part in parts:
        if re.search(r"(Asia/|UTC|GMT|[+-]\d)", part, flags=re.I) or part in ("北京时间", "中国时间", "国标时间"):
            tz_token = part
            break

    # Pass 2: find location (city name in table, or Chinese place name)
    for part in parts:
        if part == tz_token:
            continue
        if _resolve_longitude(part) is not None or re.search(r"[\u4e00-\u9fff]{2,}", part):
            location = part
            break

    # Pass 3: if no location found but there are parts, use the last non-tz part
    if not location:
        for part in parts:
            if part != tz_token:
                location = part
                break

    # Edge case: single part that is both a TZ alias and a city name (e.g. "北京")
    # Treat it as location, since TZ already defaults to Asia/Shanghai
    if (
        len(parts) == 1
        and parts[0] in TZ_ALIASES
        and _resolve_longitude(parts[0]) is not None
        and re.search(r"(Asia/|UTC|GMT|[+-]\d)", parts[0], flags=re.I) is None
    ):
        tz_token = "Asia/Shanghai"
        location = parts[0]

    tz, tz_name = _resolve_tz(tz_token)
    try:
        clock = _localize(datetime(year, month, day, hour, minute), tz)
    except ValueError as exc:
        raise BirthParseError("Invalid calendar datetime / 公历日期时间无效。") from exc

    longitude = _resolve_longitude(location)
    true_solar = clock.astimezone(SHANGHAI_TZ)
    notes: list[str] = []
    if longitude is None:
        notes.append("出生地经度未知，时柱按北京时间排，未做真太阳时经度差修正。")
    else:
        true_solar = true_solar + timedelta(minutes=(longitude - 120.0) * 4.0)
        notes.append(
            f"真太阳时修正：相对东经120°，{(longitude - 120.0) * 4.0:+.1f} 分钟。"
        )

    solar = Solar(true_solar.year, true_solar.month, true_solar.day)
    lunar = Converter.Solar2Lunar(solar)
    lunar_text = _format_lunar(lunar.year, lunar.month, lunar.day, lunar.isleap)

    bazi_year = _bazi_year(true_solar)
    year_gz = _year_pillar(bazi_year)
    month_index, jie_name, jie_time, next_jie = _bazi_month(true_solar)
    month_gz = _month_pillar(year_gz[0], month_index)

    day_for_pillar = true_solar
    late_zi = true_solar.hour == 23
    if late_zi:
        day_for_pillar = true_solar + timedelta(days=1)
        notes.append("晚子时（23:00后）：日柱按次日计算，时柱仍为子时。")
    day_gz = _day_pillar(day_for_pillar)

    hour_idx, hour_label, boundary = _hour_branch_index(true_solar.hour, true_solar.minute)
    if boundary:
        notes.append(f"出生时刻落在时辰整点（{true_solar.strftime('%H:%M')}），时柱按{BRANCHES[hour_idx]}时。")
    hour_gz = _hour_pillar(day_gz[0], hour_idx)

    hidden_text, gods_text = _hidden_and_gods(year_gz, month_gz, day_gz, hour_gz)
    next_txt = f"，下一节{next_jie}" if next_jie else ""
    jie_note = (
        f"月令节气：{jie_name}后{next_txt}"
        f"（{jie_name}于{jie_time.strftime('%Y-%m-%d %H:%M')} 北京时间）"
        f"；八字年以立春分界为{bazi_year}年"
    )

    return BirthChart(
        raw_input=raw,
        clock_time=clock,
        true_solar=true_solar,
        timezone_name=tz_name,
        location=location or "未注明",
        longitude=longitude,
        lunar_text=lunar_text,
        year_gz=year_gz,
        month_gz=month_gz,
        day_gz=day_gz,
        hour_gz=hour_gz,
        hour_label=hour_label,
        hidden_stems_text=hidden_text,
        ten_gods_text=gods_text,
        jie_note=jie_note,
        notes=notes,
    )


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


def build_user_prompt(chart: BirthChart, business_plan: str, question_key: str) -> str:
    question = QUESTION_LABELS.get(question_key, question_key)
    return (
        "Please provide BPCommentary for this founder.\n\n"
        f"{chart.to_prompt_block()}\n\n"
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
        "First: send your birth date and time as YYYY-MM-DD HH:MM, timezone, location.\n"
        "首先：请按「YYYY-MM-DD HH:MM, 时区, 出生地」发送出生时间。\n\n"
        "Example / 示例：1992-08-15 14:30, Asia/Shanghai, 北京\n\n"
        "Send /cancel anytime to stop. / 随时发送 /cancel 取消。"
    )
    return BIRTH


async def receive_birth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.message is not None
    birth = (update.message.text or "").strip()
    try:
        chart = parse_birth_input(birth)
    except BirthParseError as exc:
        await update.message.reply_text(str(exc))
        return BIRTH
    except Exception:
        logger.exception("Birth date conversion failed")
        await update.message.reply_text(
            "Could not convert this birth date. Please check the format and try again.\n"
            f"{BIRTH_FORMAT_HELP}"
        )
        return BIRTH

    context.user_data["birth"] = birth
    context.user_data["birth_chart"] = chart
    await update.message.reply_text(chart.to_user_message())
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
    chart = context.user_data.get("birth_chart")
    business_plan = str(context.user_data.get("business_plan", "")).strip()
    if not birth or not business_plan or not isinstance(chart, BirthChart):
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
        commentary = await generate_commentary(chart, business_plan, question_key)
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


async def generate_commentary(
    chart: BirthChart, business_plan: str, question_key: str
) -> str:
    payload = {
        "model": TOGETHER_MODEL,
        "max_tokens": 8192,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(chart, business_plan, question_key)},
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
            "1) Birth date and time as YYYY-MM-DD HH:MM, timezone, location (BaZi)\n"
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
