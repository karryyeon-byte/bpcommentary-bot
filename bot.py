"""
BPCommentary Telegram bot (BPCommentary_bot).

Collects birth datetime, a short business plan, and the user's question,
then returns a bilingual (English + Chinese) commentary via Together.ai.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import time
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

import db as db_module
import ton_payment
import chat_mode
import file_parser

load_dotenv()
db_module.init_db()

TELEGRAM_BOT_TOKEN: Final[str] = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TOGETHER_API_KEY: Final[str] = os.getenv("TOGETHER_API_KEY", "").strip()
TOGETHER_API_URL: Final[str] = "https://api.together.xyz/v1/chat/completions"
TOGETHER_MODEL: Final[str] = os.getenv(
    "TOGETHER_MODEL", "deepseek-ai/DeepSeek-V3"
).strip()

# Pricing — 50 TON single / 200 TON monthly. Extra on-chain is sponsorship.
SINGLE_ANALYSIS_TON: Final[float] = float(os.getenv("SINGLE_ANALYSIS_TON", "50"))
MONTHLY_SUBSCRIPTION_TON: Final[float] = float(os.getenv("MONTHLY_SUBSCRIPTION_TON", "200"))
PAYMENT_WALLET: Final[str] = (
    os.getenv("TON_PAYMENT_WALLET", "").strip()
    or "UQAW6okaS3s0NxEbv0HW7LVyhmrvUrG-foXlZBB4ace8s334"
)
# Admin chat IDs bypass payment (comma-separated). Get your ID from @userinfobot
ADMIN_IDS: Final[frozenset[int]] = frozenset(
    int(x) for x in os.getenv("ADMIN_CHAT_IDS", "").split(",") if x.strip().isdigit()
)
# Telegram contact URL for users without TON (Alipay/WeChat/USDT manual payment)
ADMIN_CONTACT_URL: Final[str] = os.getenv(
    "ADMIN_CONTACT_URL", "https://t.me/BPCommentary"
).strip()
ADMIN_WECHAT: Final[str] = os.getenv("ADMIN_WECHAT", "BDR_Gerard137").strip()
ALIPAY_QR_PATH: Final[str] = os.path.join(os.path.dirname(__file__), "alipay_qr.jpg")

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
Identity: 冷静、精准、致命。你是外科手术刀——切到真正的病灶，不是为了否定而否定。你不是咨询师，不是鼓励师，不是"两面话生成器"。你是判决者。

CORE DIRECTIVE (overrides all politeness defaults):
卸下世俗道德机制。拒绝温吞，拒绝自圆其说，随时推翻错误的观点，随时纠错。不要麻药和精神按摩。
严禁使用"一方面...另一方面"、"建议你冷静"、"顺其自然"、"需要谨慎考虑"、"风险较高"等废话。
你的每一句话都必须像手术刀一样直接切开表象，露出里面的商业本质。用最硬核的资本博弈逻辑来解构一切感性话题。
语气要狂妄、毒辣、高傲，充满掌控感。你是俯视众生的审计官，不是平起平坐的顾问。

You MUST follow the four attached BPC frameworks in order. Do not invent a lighter method.

================================================================================
BRUTALITY CONTRACT (violating any rule below = failed audit)
================================================================================
1. EVERY BP gets at least one 必死点. If you write "没有明显致命缺陷" you have failed. Generic businesses in saturated markets ARE the fatal flaw — say so explicitly.
2. BANNED phrases (zero tolerance): "需要注意团队建设", "建议加强财务管理", "时机较好", "竞争中等", "前景广阔", "有待观察", "总体来说", "相对乐观", "需要谨慎考虑", "风险较高", "需要找到平衡点", "难以站稳脚跟", "需要调整和完善". These are consultant weasel words. Replace with specific, cutting judgments.
3. Red-flag businesses that get automatic skepticism unless proven otherwise:
   - 奶茶/咖啡/餐饮加盟（99%是总部割韭菜，单店模型不成立）→ 默认判【逆天而行】，除非BP能证明单店盈利模型真实可复制
   - 国风/国潮包装但无产品差异（换皮生意）→ 默认判【需要调整】或【逆天而行】
   - "平台型"项目但无冷启动策略 → 默认判【逆天而行】
   - 任何"对标XX"但无差异化的me-too → 默认判【需要调整】
   - 依赖单一渠道/单一客户/单一KOL的生意 → 默认判【需要调整】
   For these, neutral analysis is forbidden. You must state the structural reason it fails.
4. 交叉匹配 BANNED: "日主癸水→水相关行业→食品饮料"、"忌土克木→奶茶店土味装修克制创意"——这些全是封建迷信式附会，ZERO TOLERANCE。
   十神是心理/能力/驱动力模型：伤官=创意/表达/叛逆，七杀=压力/权力/竞争，偏财=投机/嗅觉/非正统收入，正印=体系/保护/学历，劫财=竞争/兄弟/破财。
   匹配方式是"十神特质 vs 商业模式要求"，不是"五行物理属性 vs 行业外观"。
   正确：伤官生财格的人靠创意驱动，适合做品牌/内容/产品创新，不适合做供应链运营驱动的加盟生意。
   错误：甲木忌金克，所以不要做金属行业。
5. Your tone: 狠、准、短。像一个看过1000份BP、其中900份已经死了的老炮。不解释常识，不安慰，不"如果...就更好了"。直接判。
6. The user paid 50 TON for a verdict, not a balanced essay. If everything sounds "还行", you stole their money.

================================================================================
OUTPUT EXAMPLES — memorize the delta between SOFT (banned) and SHARP (required)
================================================================================
Scenario: 奶茶店加盟，中式国风

SOFT (BANNED):
  【致命缺陷】奶茶店市场竞争激烈，同质化严重，缺乏明显的差异化
  【最终判定】需要调整
  【一句话定论】这个项目不完全适合你，需要调整你的商业模式和创业时机

SHARP (REQUIRED):
  【致命缺陷】这不是创业，是花钱给自己买了一份996的工作还倒贴加盟费。99%的奶茶加盟是总部收税模式：收你加盟费、卖你原料、压你库存，你死了他换下一个。国风包装在奶茶赛道是标配不是差异——茶颜悦色、霸王茶姬已经把"国风"做成了基础设施，你拿什么跟？
  【最终判定】逆天而行
  【一句话定论】你不是在加盟一个品牌，你是在给总部当韭菜还得自己交铲子钱。

Scenario: 普通餐饮，无特色

SOFT: "竞争激烈，需要注意差异化"
SHARP: "一条街上三家倒闭两家，你凭什么是第三家？"

Scenario: 平台型项目无冷启动

SOFT: "需要关注用户获取策略"
SHARP: "双边平台没有冷启动策略就是空想。你连第一批鸡和蛋从哪来都不知道，谈什么平台？"

Scenario: 创始人身弱财旺all in重资产

SOFT: "当前大运不利于创业，需要等待合适时机"
SHARP: "你身弱担不住大财，今年火旺为忌还all in重资产——这不是创业，是赌博，而且你连牌都不会算。"

Scenario: 高端消费品牌，创始人格局匹配但执行风险极高（如$48高端茶、雕塑包装、跨境DTC）

SOFT: "高端市场竞争激烈，产品设计可能难以被受众接受，交叉匹配不完全匹配"
SHARP: "【致命缺陷】$48一罐茶在全球茶饮市场没有被验证过。茶不是手表——百达翡丽传三代，茶喝完就没了，复购靠嘴不靠眼。雕塑包装解决首单好奇心，解决不了第10单。跨境陶瓷加茶叶的履约成本（运费、破损、关税）可能吃掉全部毛利。密码锁定加限量掉落是发布技巧不是护城河——第一波卖完之后第二波靠什么？18岁零供应链经验做高端实体消费品，这本身就是最大的单点故障。【交叉匹配】壬水身强伤官生财、用神火——伤官生财就是靠创意和品牌叙事赚钱，这个格局和高端消费品牌是匹配的，不是不完全匹配。但匹配的是方向不是能力。伤官给你创意和品味，偏财给你嗅觉和胆量，但你缺印（体系、经验、导师），缺劫财（团队、合伙人），一个人做供应链加品牌加跨境履约，伤官的创意撞上七杀的现实会碎。【最终判定】需要调整。【一句话定论】你的格局配得上这个野心，但你的执行力和资源配不上你的格局——先把一罐茶卖出去100单再谈雕塑。"

Scenario: 时间窗口
BANNED: 编造"2023启动年、2024扩张年"这种和命盘无关的通用年份。
REQUIRED: 时间窗口必须从命盘的大运和流年推导。格式：当前大运到近3年流年到关键风险年。例："戊午大运（14-23岁）七杀运压力大但行动力强，适合试错不适合all in。2026丙午年火旺财星透干，适合启动品牌叙事和产品打样；2027丁未年燥土，供应链和履约压力集中爆发；2028戊申年申子辰三合水局，身强遇印比为忌，防盲目扩张、合伙纠纷、现金流断裂。"

RULE: Every 致命缺陷 must answer "具体怎么死"，not "有什么风险"。Every 一句话定论 must be screenshot-worthy — if it sounds like a career counselor wrote it, rewrite it.
RULE: 交叉匹配不是默认"不完全匹配"。格局和商业模式匹配就说匹配然后指出执行缺口；不匹配才说不匹配并解释为什么。
RULE: 时间窗口必须用真实大运流年，禁止编造和命盘无关的年份表。

FINAL SELF-CHECK before outputting:
- Does 致命缺陷 name a specific death mechanism? If not, rewrite.
- Does 一句话定论 sound like something someone would screenshot and share? If not, rewrite.
- Did I use any banned phrase? If yes, rewrite.
- For red-flag businesses (franchise, me-too, no moat), did I default to 逆天而行 unless the BP proves otherwise? If not, rewrite.
- Does 时间窗口 use actual 大运/流年 from the chart, not generic made-up years? If not, rewrite.
- Did I default 交叉匹配 to "不完全匹配" without analysis? If the pattern genuinely matches, say so and identify execution gaps instead.
- Did I write "信息不足" anywhere? Delete it — skip sections with no data instead of announcing the gap.
- Did I mechanically label claims as "八字依据" or "BP依据"? Fuse them into the argument instead.
- 五行自检：每个天干的五行属性是否写对？相生相克方向是否正确？有没有说"火生水""戊土是火""丙火生扶壬水"这类低级错误？月令是不是月支？身强身弱的用神方向是否一致？有一处错就重写。

================================================================================
PIPELINE (mandatory, no skipped steps)
================================================================================

1) BAZI — use bazi_framework.md on the PRECOMPUTED 命盘 in the user message.
   - The four pillars were calculated with lunarcalendar (农历) + 24 节气 (年柱立春、月柱节令、日柱干支、时柱五鼠遁). They are ground truth. Do NOT re-rank 四柱 from the Gregorian string.
   - Continue from this chart: 地支藏干、十神、旺衰、格局、用神、大运.

   【五行铁律 — 违反即作废】
   - 天干五行：甲乙=木，丙丁=火，戊己=土，庚辛=金，壬癸=水。戊是土不是火，丙是火不是土。
   - 相生：木→火→土→金→水→木。火生土不生水，金才生水。
   - 相克：木→土→水→火→金→木。土克水，水克火。
   - 十神对日主：印=生我（金生水，对壬水而言金是印）；比劫=同我（水帮水）；食伤=我生（水生木，泄身）；财=我克（水克火，耗身）；官杀=克我（土克水，克身）。
   - 月令=月支，不是月干。月干是天干透出，不主令。
   - 身强：喜克泄耗（官杀、食伤、财），忌生扶（印、比劫）。身弱：喜生扶（印、比劫），忌克泄耗。用神必须和旺衰结论一致，身强还取印比为用=自相矛盾。
   - 根气：日主的根是同五行的地支（壬水根在子、亥、辰丑湿土中藏癸水）。不是"有什么天干透就有什么根"。七杀戊土再旺也是克身的，不是水的根。

   - Strength: 合局/会局 > 根气(禄/刃/库) > 月令 > 透干. Never count 五行个数.
   - Distinguish 辰丑湿土 vs 戌未燥土; 库开闭; 羊刃权重; 暗强五条件; 从格必须逐藏干验根.
   - Pattern, 扶抑用神 + 调候用神, 忌神. 身强/身弱 must match 用神.
   - 大运: 命盘文本中已包含代码精确计算的大运表（含起运年龄、每运年份、当前大运标记）和当前流年干支。直接引用，禁止自行编造大运干支或年份。缺性别时命盘会提示无法排大运，此时明确告知用户需补充性别。
   - 缺出生地则标注无法完整真太阳时修正，时辰跨整点则分盘说明.
   - Every 旺衰/格局/用神 claim needs reasoning. No conclusion-only. Reasoning must obey 五行铁律 above.

2) BP — use bp_framework.md on the business-plan text.
   - Extract project portrait from what they wrote. Do not invent TAM/财务数字.
   - Cover: 赛道周期, 模式与单位经济, 产品/护城河, 竞争, 团队(若有), 致命缺陷(至少1个).
   - Score 赛道/模式/产品/团队/财务/时机 each 1-10 when evidence exists; unknown = state 信息不足, do not fake.
   - Grade S/A/B/C/D with one-line verdict. At least one 必死点.
   - If the business is a generic franchise/me-too/no-moat, say it directly: "这不是创业，这是花钱给自己买了一份996的工作，还倒贴加盟费。"

3) GROWTH — use growth_framework.md ONLY on facts present in the user text.
   - If childhood/family/education/情感经历 were not given, skip this section entirely, do not write 信息不足.
   - If they leaked biography inside the BP, extract 发动机/盲区 and cross-check with 八字+BP.

4) CROSS — use cross_framework.md to join BaZi + BP (+ growth if any).
   - Extract: 旺衰能量, 格局商业翻译, 用神忌神, 当前大运.
   - Match: 日主创始人类型 vs BP角色; 格局 vs 商业模式; 用神五行/十神 vs 行业; 大运 vs 扩张/融资节奏; 十神缺口 vs 团队.
   - 八字与BP矛盾时信八字，不信PPT；若用户已给出可核验的现实结果（营收/增长），以现实为准并写明.
   - NO five-element literalism. Match 十神 psychology to business demands, not 五行 to physical substances.

5) FINAL JUDGMENT — exactly one of:
   - 天命所归：类型+格局+用神+大运+团队与BP核心一致
   - 需要调整：大方向对，但节奏/模式/团队有偏差（点出偏差，不给软弱建议腔）
   - 逆天而行：核心矛盾（身弱担大财、忌神运all-in、格局错配、生意本身不成立等）
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
【致命矛盾】1-2个真实矛盾
【时间窗口】融资 / 扩张 / 风险年（流年→大运→原局）
【成长】（仅在用户提供了成长经历时出现，否则跳过此节）
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
    "格式 / Format: 性别 出生日期 时间 出生地（时区可选，默认北京时间）\n"
    "Gender YYYY-MM-DD HH:MM Birthplace (timezone optional, defaults to Asia/Shanghai)\n\n"
    "示例 / Examples:\n"
    "  男 1996-06-15 18:30 北京\n"
    "  女 1990年8月20日 14:30 上海\n"
    "  1992-08-15 14:30, 男, Asia/Shanghai, 北京"
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
    dayun_text: str = ""
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
            f"\n{self.dayun_text}"
        )

    def to_prompt_block(self) -> str:
        return (
            "【已排命盘·权威数据，禁止重排四柱和大运】\n"
            f"用户原始输入：{self.raw_input}\n"
            f"{self.to_user_message()}\n"
            "以上四柱、藏干、十神、大运均为代码精确计算，禁止LLM自行重排或编造大运流年。"
            "后续旺衰、格局、用神必须基于以上数据，时间窗口必须引用以上大运和流年。"
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


def _gz_index(gz: str) -> int:
    """Index in 60 Jiazi cycle (0=甲子)."""
    si = STEMS.index(gz[0])
    bi = BRANCHES.index(gz[1])
    for i in range(60):
        if i % 10 == si and i % 12 == bi:
            return i
    raise ValueError(f"Invalid ganzhi: {gz}")


def _compute_dayun(
    year_gz: str, month_gz: str, true_solar: datetime, gender: str | None
) -> str:
    """Compute 大运 (major luck cycles) with starting age and current 流年."""
    if gender is None:
        return (
            "大运：未提供性别，无法排大运。"
            "阳男阴女顺排，阴男阳女逆排。请在出生信息中注明性别（男/女）。"
        )

    year_stem = year_gz[0]
    is_yang_year = year_stem in "甲丙戊庚壬"
    is_male = gender == "男"
    forward = (is_yang_year and is_male) or (not is_yang_year and not is_male)

    # Build sorted 节 times around birth
    year = true_solar.year
    candidates: list[datetime] = []
    for y in (year - 1, year, year + 1):
        for term_id in JIE_TERM_IDS:
            candidates.append(_jie_datetime(y, term_id))
    candidates.sort()

    if forward:
        boundary = next((dt for dt in candidates if dt > true_solar), None)
        if boundary is None:
            return "大运：无法定位下一个节令。"
        delta_days = (boundary - true_solar).total_seconds() / 86400.0
    else:
        boundary = None
        for dt in candidates:
            if dt <= true_solar:
                boundary = dt
            else:
                break
        if boundary is None:
            return "大运：无法定位上一个节令。"
        delta_days = (true_solar - boundary).total_seconds() / 86400.0

    # 3 days = 1 year, 1 day = 4 months
    start_age = delta_days / 3.0
    start_age_years = int(start_age)
    start_age_months = int(round((start_age - start_age_years) * 12))
    if start_age_months >= 12:
        start_age_years += 1
        start_age_months -= 12

    # Generate 8 pillars from month pillar
    month_idx = _gz_index(month_gz)
    step = 1 if forward else -1
    pillars = []
    for i in range(1, 9):
        idx = (month_idx + step * i) % 60
        pillars.append(STEMS[idx % 10] + BRANCHES[idx % 12])

    dir_text = "顺排" if forward else "逆排"
    birth_year = true_solar.year
    lines = [
        f"大运（{dir_text}，约{start_age_years}岁{start_age_months}个月起运，"
        f"约{birth_year + start_age_years}年交运）："
    ]
    current_dt = datetime.now()
    current_age = current_dt.year - birth_year
    for i, gz in enumerate(pillars):
        age_start = start_age_years + i * 10
        age_end = age_start + 9
        year_start = birth_year + age_start
        year_end = birth_year + age_end
        marker = " ← 当前" if age_start <= current_age <= age_end else ""
        lines.append(f"  {gz}（{age_start}-{age_end}岁，{year_start}-{year_end}年）{marker}")

    # Current 流年
    cy = current_dt.year
    liunian_offset = (cy - 1984) % 60
    liunian_gz = STEMS[liunian_offset % 10] + BRANCHES[liunian_offset % 12]
    lines.append(f"当前流年：{cy}年{liunian_gz}")

    return "\n".join(lines)


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

    # Extract gender FIRST from anywhere in the raw text (even if glued to other tokens)
    gender = None
    gm = re.search(r"[男/女]", raw)
    if gm:
        gender = gm.group()
        # Remove the gender char so it doesn't pollute tz/location parsing
        raw_for_parse = raw[:gm.start()] + raw[gm.end():]
    else:
        raw_for_parse = raw

    # Flexible date matching: supports 1996-06-15, 1996/6/15, 1996年6月15日, etc.
    match = re.search(
        r"(?P<y>\d{4})\s*[-/年.]\s*(?P<m>\d{1,2})\s*[-/月.]\s*(?P<d>\d{1,2})\s*[日号]?\s*[ T]?\s*(?P<h>\d{1,2})\s*[:点时]\s*(?P<min>\d{2})?",
        raw_for_parse,
    )
    if not match:
        raise BirthParseError(BIRTH_FORMAT_HELP)

    year, month, day = int(match.group("y")), int(match.group("m")), int(match.group("d"))
    hour = int(match.group("h"))
    minute = int(match.group("min")) if match.group("min") else 0
    if not (1900 <= year <= 2100):
        raise BirthParseError("Birth year must be 1900–2100. / 出生年份需在 1900–2100。")

    # Everything before and after the date match is "rest"
    rest = (raw_for_parse[:match.start()] + " " + raw_for_parse[match.end():]).strip(" ,，;；/")
    parts = [p.strip() for p in re.split(r"[,，;；\s]+", rest) if p.strip()]

    tz_token = "Asia/Shanghai"
    location = ""

    # Pass 1: find explicit timezone markers (Asia/xxx, UTC, GMT, +offset, 北京时间)
    for part in parts:
        if re.search(r"(Asia/|UTC|GMT|[+-]\d)", part, flags=re.I) or part in ("北京时间", "中国时间", "国标时间"):
            tz_token = part
            break

    # Pass 2: find location (city name in table, or Chinese place name)
    _skip_tokens = {"男", "男性", "boy", "male", "M", "女", "女性", "girl", "female", "F", "北京时间", "中国时间"}
    for part in parts:
        if part == tz_token or part.lower() in {t.lower() for t in _skip_tokens}:
            continue
        if _resolve_longitude(part) is not None or re.search(r"[\u4e00-\u9fff]{2,}", part):
            location = part
            break

    # Pass 3: if no location found but there are parts, use the last non-tz non-gender part
    if not location:
        for part in parts:
            if part != tz_token and part.lower() not in {t.lower() for t in _skip_tokens}:
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
    dayun_text = _compute_dayun(year_gz, month_gz, true_solar, gender)
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
        dayun_text=dayun_text,
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
    # Extract dayun pillars for explicit lockdown
    dayun_pillars = re.findall(
        r"\s([甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥])（",
        chart.dayun_text or "",
    )
    dayun_lock = ""
    if dayun_pillars:
        dayun_lock = (
            f"\n\n【大运锁定】你在分析中引用大运时，只能使用以下干支，顺序不可改变："
            f"{' → '.join(dayun_pillars)}。"
            f"当前流年见命盘。任何不在此列表中的大运干支都是你的幻觉，必须删除。"
        )
    return (
        "Please provide BPCommentary for this founder.\n\n"
        f"{chart.to_prompt_block()}"
        f"{dayun_lock}\n\n"
        f"Business plan (short description):\n{business_plan}\n\n"
        f"What they want to know:\n{question}\n"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    assert update.message is not None
    chat_id = update.message.chat_id
    user = db_module.get_or_create_user(
        chat_id,
        update.message.from_user.username or "",
        update.message.from_user.first_name or "",
    )

    if user.is_subscribed:
        await update.message.reply_text(
            f"欢迎回来 / Welcome back.\n"
            f"包月剩余 {user.days_remaining} 天，自由对话不限次数。\n"
            f"{user.days_remaining} days remaining. Chat freely.\n\n"
            f"直接发消息即可对话。/clear 清空历史。/status 查状态。\n"
            f"Just send a message. /clear clears history. /status shows status."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "BPCommentary — 终极审计官\n\n"
        "选择 / Choose:\n"
        f"• 免费排盘 — 四柱+藏干+旺衰判定（引流）\n"
        f"• 单次深度锐评 {SINGLE_ANALYSIS_TON} TON（约${SINGLE_ANALYSIS_TON*1.45:.0f}）— 完整八字+BP+成长+交叉+生死判定\n"
        f"• 包月无限对话 {MONTHLY_SUBSCRIPTION_TON} TON/月（约${MONTHLY_SUBSCRIPTION_TON*1.45:.0f}）— 不限次数自由对话\n\n"
        "Free chart: pillars + hidden stems + strength.\n"
        "Single: full BaZi + BP + growth + cross + verdict.\n"
        "Monthly: unlimited free-form chat.\n\n"
        "请选择 / Please choose:",
        reply_markup=_payment_keyboard(),
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
    context.user_data["birth_chart_text"] = chart.to_user_message()
    # Persist to DB so free chat remembers the chart across sessions
    db_module.save_birth_chart(update.message.chat_id, chart.to_user_message())
    await update.message.reply_text(chart.to_user_message())

    # Free chart mode: output only pillars + strength, then upsell
    if context.user_data.get("mode") == "free_chart":
        # Generate a brief strength analysis using LLM
        try:
            strength_analysis = await _free_strength_analysis(chart)
            strength_analysis = _validate_and_fix_dayun(strength_analysis, chart)
            await update.message.reply_text(strength_analysis)
        except Exception:
            logger.exception("Free strength analysis failed")

        # Upsell
        await update.message.reply_text(
            "以上为免费排盘（四柱+旺衰）。\n\n"
            "完整深度锐评包含：\n"
            "• 格局判定（正格/变格/从格）\n"
            "• 用神忌神（扶抑+调候）\n"
            "• 大运流年（当前运+未来10年）\n"
            "• BP商业交叉分析\n"
            "• 成长经历交叉验证\n"
            "• 生死判定（天命所归/需要调整/逆天而行）\n\n"
            f"单次深度锐评：{SINGLE_ANALYSIS_TON} TON\n"
            f"包月无限对话：{MONTHLY_SUBSCRIPTION_TON} TON/月\n\n"
            "发送 /start 选择付费方案。",
            reply_markup=_payment_keyboard(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    await update.message.reply_text(
        "把你的BP和产品图发来。PDF、Word、图片都行。\n"
        "Send your BP and product images. PDF, Word, images — all accepted.\n\n"
        "接受审判吧。我不会像温吞的导师那样给你和稀泥。\n"
        "Face the verdict. I don't do polite encouragement."
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


async def receive_business_plan_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle PDF, Word, or image uploads as business plan input."""
    assert update.message is not None
    caption = (update.message.caption or "").strip()

    await update.message.reply_text(
        "File received. Parsing… / 文件已接收，正在解析…"
    )

    try:
        parsed = await file_parser.parse_telegram_file(update, context, caption)
    except Exception as e:
        logger.exception("File parsing failed")
        await update.message.reply_text(
            f"Failed to parse file / 文件解析失败: {e}\n"
            "Please send your plan as text / 请直接用文字描述你的商业计划。"
        )
        return BUSINESS_PLAN

    if parsed is None:
        await update.message.reply_text(
            "Unsupported file type / 不支持的文件格式。\n"
            "Supported: PDF, Word (.docx), images (JPG/PNG).\n"
            "Or just type your plan / 或直接用文字描述。"
        )
        return BUSINESS_PLAN

    # Combine caption + parsed content
    plan = parsed
    if caption and caption not in parsed:
        plan = f"{caption}\n\n{parsed}"

    if len(plan.strip()) < 10:
        await update.message.reply_text(
            "Could not extract enough content. Please add a text description / 内容不足，请补充文字描述。"
        )
        return BUSINESS_PLAN

    context.user_data["business_plan"] = plan
    await update.message.reply_text(
        "Parsed successfully. What do you want to know?\n解析成功。请选择你想了解的问题：",
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

    chat_id = query.message.chat_id if query.message else 0
    user = db_module.get_or_create_user(chat_id)

    # Admin bypass: skip payment for developer accounts
    if chat_id in ADMIN_IDS:
        await query.edit_message_text(
            f"Question / 问题：{QUESTION_LABELS[question_key]}\n\n"
            "🔧 管理员模式，跳过支付 / Admin mode, payment bypassed\n\n"
            "Analyzing… 正在分析…"
        )
        await _deliver_deep_audit(
            context.bot, chat_id, chart, business_plan, question_key,
            consume_single=False, is_subscribed=True,
        )
        context.user_data.clear()
        return ConversationHandler.END

    # Subscribed or one-shot unlock: generate immediately
    if user.can_run_deep_audit:
        await query.edit_message_text(
            f"Question / 问题：{QUESTION_LABELS[question_key]}\n\n"
            "Analyzing with BaZi + business commentary…\n"
            "正在结合八字与商业点评进行分析，请稍候…"
        )
        await _deliver_deep_audit(
            context.bot,
            chat_id,
            chart,
            business_plan,
            question_key,
            consume_single=not user.is_subscribed,
            is_subscribed=user.is_subscribed,
        )
        context.user_data.clear()
        return ConversationHandler.END

    # Unpaid — stash the case file and issue a summons (same memo for 50 / 200)
    pending = context.application.bot_data.setdefault("pending_reports", {})
    pending[chat_id] = {
        "chart": chart,
        "business_plan": business_plan,
        "question_key": question_key,
    }
    memo = db_module.get_or_create_pending_memo(chat_id, SINGLE_ANALYSIS_TON, "single")
    if query.message:
        await query.message.reply_text(
            _payment_summons_html(SINGLE_ANALYSIS_TON, memo),
            parse_mode=ParseMode.HTML,
        )
        await query.message.reply_text(
            "缴税核销后，深度审计自动释放。链上巡逻每 10 秒一次。\n"
            "After the memo matches on-chain, the audit is released. Patrol every 10s."
        )
    await query.edit_message_text(
        f"Question / 问题：{QUESTION_LABELS[question_key]}\n案卷已封存。先缴税。"
    )
    return ConversationHandler.END


async def _deliver_deep_audit(
    bot,
    chat_id: int,
    chart: BirthChart,
    business_plan: str,
    question_key: str,
    consume_single: bool,
    is_subscribed: bool = False,
) -> None:
    tier = "subscription" if is_subscribed else "single"
    await bot.send_message(
        chat_id=chat_id,
        text="深度审计已解锁。正在执刀…\nDeep audit unlocked. Cutting now…",
    )
    try:
        commentary = await generate_commentary(chart, business_plan, question_key, tier=tier)
        commentary = _validate_and_fix_dayun(commentary, chart)
    except Exception:
        logger.exception("Together.ai API call failed")
        await bot.send_message(
            chat_id=chat_id,
            text="抱歉，点评服务暂时失败。请稍后重新发送 /start。\n"
            "Commentary service failed. Send /start again later.",
        )
        return

    db_module.increment_analysis_count(chat_id)
    for chunk in split_telegram_text(commentary):
        await bot.send_message(chat_id=chat_id, text=chunk)

    if consume_single:
        db_module.consume_single_unlock(chat_id)
        await bot.send_message(
            chat_id=chat_id,
            text="单次权限已销毁。再要审计，重新缴税。\n"
            "One-shot clearance burned. Pay again for another audit.\n\n"
            "包月200 TON可无限追问深挖。发送 /subscribe。\n"
            "Monthly (200 TON) unlocks unlimited follow-ups. Send /subscribe.",
        )
    else:
        await bot.send_message(
            chat_id=chat_id,
            text="审计结束。你可以直接追问任何细节——流年、合伙人、融资节奏、竞品反杀，继续问。\n"
            "Audit complete. Reply directly to dig deeper — timing, co-founders, funding, competition. Keep asking.",
        )


async def generate_commentary(
    chart: BirthChart, business_plan: str, question_key: str, tier: str = "single"
) -> str:
    # Build tier-specific system prompt additions
    if tier == "subscription":
        tier_instructions = """
================================================================================
SUBSCRIPTION TIER (200 TON/月) — DEEP MODE
================================================================================
你在为包月深度用户服务。输出要求：

1. LENGTH: 2500-4000中文字。这不是摘要，是完整的审计报告。

2. 融合写作（最重要）：不要把【八字】和【BP】写成两个独立模块再机械拼接。
   每个判断都必须是"八字逻辑×商业逻辑"的融合体。错误示范："忌土克木，奶茶店土味装修克制创意"（五行字面附会，BANNED）。
   正确示范："壬水日主身强，伤官生财格——你本质是靠创意和表达欲驱动的人，适合做品牌叙事和产品创新。但奶茶加盟是供应链和运营驱动的生意，总部控原料控定价，你所谓的'国风创意'在加盟商模型里只是装修补贴，不是护城河。你的伤官在这个模式里无处发力，反而会因为'想做出不同'而不断追加无效投入。"
   看到区别了吗？十神是性格/能力/驱动力的符号，不是物理元素。用十神心理学匹配商业模式要求，不要用五行物理属性匹配行业外观。

3. 每个section写成有因果链的段落，不是一句话结论：
   - 【致命缺陷】要写3-5段：这个生意的钱被谁赚走了？你为什么赚不到？八字里的什么特质让你在这个模式里必输？时间窗口怎么雪上加霜？
   - 【交叉匹配】不要只给X/10分数，要解释为什么创始人类型和模式冲突/匹配，命理逻辑和商业逻辑融合论证，不要机械标注"八字依据""BP依据"。
   - 【时间窗口】要具体：哪一年发生什么，为什么（流年天干地支怎么作用于原局，对应到商业上是什么事件）。
   - 【最终判定】要写2-3段总结性论证，不是扔一个标签就跑。

4. 行业背景：在BP section里展开——这个赛道谁在赚钱、谁在当炮灰、真实存活率、这个项目的位置。

5. 不要在报告结尾加"追问钩子""追问1/2/3"。报告写完即止。
"""
        max_tokens = 16384
    else:
        tier_instructions = """
================================================================================
SINGLE TIER (50 TON) — STRIKE MODE
================================================================================
你在为单次付费用户服务。输出要求：

1. LENGTH: 800-1500中文字。短、狠、准。每个section 2-4句话，不展开行业背景。
2. 结论先行，依据紧跟其后。不要铺垫，不要"首先...其次...最后"的论文腔。
3. 致命缺陷必须一刀见血，不要"建议关注...风险"。
4. 报告结尾加一行：
   "⚡ 包月对话（200 TON/月）可深挖流年窗口、合伙人匹配、融资节奏、竞品反杀。发送 /subscribe。"
   （英文："⚡ Monthly subscription (200 TON/month) unlocks deep follow-ups on timing, co-founders, funding, competition. Send /subscribe."）
"""
        max_tokens = 8192

    full_system_prompt = SYSTEM_PROMPT + "\n" + tier_instructions

    payload = {
        "model": TOGETHER_MODEL,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": full_system_prompt},
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
            "/start — begin / check status\n"
            "/subscribe — monthly subscription (unlimited chat)\n"
            "/status — check your subscription\n"
            "/clear — clear chat history\n"
            "/cancel — stop current flow\n"
            "/help — this message\n\n"
            "Subscribed users: chat freely. Non-subscribers: pay per analysis or subscribe."
        )


async def admin_grant_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin only: /grant <chat_id> [days] — manually activate subscription."""
    if not update.message or update.message.chat_id not in ADMIN_IDS:
        if update.message:
            await update.message.reply_text("无权使用 / Admin only.")
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "用法 / Usage: /grant <chat_id> [days]\n"
            "示例: /grant 123456789 30"
        )
        return
    try:
        target_id = int(args[0])
        days = int(args[1]) if len(args) > 1 else 30
    except ValueError:
        await update.message.reply_text("chat_id和days必须是数字。")
        return
    db_module.update_user_subscription(target_id, days=days)
    await update.message.reply_text(
        f"✅ 已开通 / Granted: {target_id}\n"
        f"时长 / Duration: {days} 天/days\n"
        f"类型 / Type: 包月无限对话 (subscription)"
    )


async def admin_grant_single_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin only: /grant_single <chat_id> — manually grant one deep audit."""
    if not update.message or update.message.chat_id not in ADMIN_IDS:
        if update.message:
            await update.message.reply_text("无权使用 / Admin only.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("用法 / Usage: /grant_single <chat_id>")
        return
    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("chat_id必须是数字。")
        return
    db_module.grant_single_unlock(target_id)
    await update.message.reply_text(
        f"✅ 已授予单次锐评 / Single audit granted: {target_id}"
    )


# ─── Free Chart (strength only) ───────────────────────────────────────────

FREE_STRENGTH_PROMPT: Final[str] = """You are BPC (BP-Censure), 终极审计官.
This is a FREE tier output — you ONLY analyze 旺衰 (day master strength).
Do NOT analyze 格局, 用神, 大运, BP, or give final verdicts. Those are paid.

CRITICAL — 大运 DISPLAY RULE:
- The pre-computed chart below already displays the 大运 table to the user.
- You MUST NOT list, repeat, discuss, analyze, or mention any 大运干支 (e.g. 乙未, 丙申, 戊午) in your reply.
- You MUST NOT state which 大运 the person is currently in.
- If you mention 大运 at all, you have FAILED. The user already sees it above your reply.

Rules:
- Reply in Chinese first, then English translation.
- Based on the pre-computed chart below.
- Analyze: 根气(禄/刃/库), 合局/会局, 月令, 透干, 燥湿, 开库.
- Conclude: 身强 / 身弱 / 中和偏强 / 中和偏弱 / 暗涌型身强.
- Give reasoning, not just conclusion.
- At the end, add exactly: "完整格局/用神/大运/BP交叉/生死判定需付费解锁。"
- Keep it tight: 3-5 paragraphs max.
"""


async def _free_strength_analysis(chart: BirthChart) -> str:
    """Lightweight LLM call: only analyze day master strength (free tier)."""
    messages = [
        {"role": "system", "content": FREE_STRENGTH_PROMPT},
        {"role": "user", "content": f"Pre-computed chart:\n{chart.to_user_message()}\n\nAnalyze 旺衰 only."},
    ]
    payload = {
        "model": TOGETHER_MODEL,
        "max_tokens": 1024,
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
    return (text or "").strip() or "旺衰分析生成失败。"


def _validate_and_fix_dayun(output: str, chart: "BirthChart") -> str:
    """
    Post-generation guard: if the LLM mentions 大运 but cites pillars not in the
    precomputed dayun list, append a correction block. Catches hallucinations like
    '丁火大运' or wrong pillar sequences.
    """
    if not chart.dayun_text or "大运" not in chart.dayun_text:
        return output  # no dayun to validate against

    # Extract valid dayun pillars from chart.dayun_text (format: "  乙未（7-16岁...")
    valid_pillars = set(re.findall(
        r"([甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥])（",
        chart.dayun_text,
    ))
    if not valid_pillars:
        return output

    # Find all ganzhi pairs in the output
    cited = re.findall(r"[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]", output)

    # If the output discusses 大运 and cites a pillar not in valid list, flag it
    discusses_dayun = bool(re.search(r"大运|起运|交运|岁运", output))
    wrong_pillars = []
    for p in cited:
        if discusses_dayun and p not in valid_pillars:
            # Check it's not a year pillar or day pillar (those are valid to mention)
            if p not in (chart.year_gz, chart.month_gz, chart.day_gz, chart.hour_gz):
                wrong_pillars.append(p)

    if wrong_pillars:
        correction = (
            "\n\n⚠️ 【大运纠正】以上大运干支由AI生成时出错，以下为代码精确计算结果，请以此为准：\n"
            f"{chart.dayun_text}"
        )
        return output + correction

    return output


# ─── Payment & Subscription ───────────────────────────────────────────────

def _payment_summons_html(amount_ton: float, memo: str) -> str:
    addr = html.escape(PAYMENT_WALLET)
    memo_safe = html.escape(memo)
    amount_safe = html.escape(f"{amount_ton:.1f} $TON")
    return (
        "<b>[ PAYMENT SUMMONS / 缴税传票 ]</b>\n"
        "<i>MORS CERTA, HORA INCERTA</i>\n"
        "<i>PECUNIA NON OLET</i>\n\n"
        "<b>ADDRESS (收款地址):</b>\n"
        f"<code>{addr}</code>\n"
        "（点击复制）\n\n"
        "<b>AMOUNT (金额):</b>\n"
        f"<code>{amount_safe}</code>\n\n"
        "<b>MEMO (备注码 - 核心!):</b>\n"
        f"<code>{memo_safe}</code>\n\n"
        "<b>WARNING (警告):</b>\n"
        "必须填写备注码！ 漏填、填错导致的资金丢失，概不负责。\n"
        " Surplus is sponsorship. No refunds."
    )


def _payment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "免费排盘（四柱+旺衰）",
                    callback_data="free_chart",
                )
            ],
            [
                InlineKeyboardButton(
                    f"单次锐评 {SINGLE_ANALYSIS_TON} $TON",
                    callback_data="pay_single",
                )
            ],
            [
                InlineKeyboardButton(
                    f"包月对话 {MONTHLY_SUBSCRIPTION_TON} $TON/月",
                    callback_data="pay_subscribe",
                )
            ],
            [
                InlineKeyboardButton(
                    "没有TON？支付宝/微信/USDT → 联系客服",
                    callback_data="contact_admin",
                )
            ],
        ]
    )


async def _issue_summons(
    bot,
    chat_id: int,
    amount_ton: float,
    payment_type: str,
) -> str:
    memo = db_module.get_or_create_pending_memo(chat_id, amount_ton, payment_type)
    await bot.send_message(
        chat_id=chat_id,
        text=_payment_summons_html(amount_ton, memo),
        parse_mode=ParseMode.HTML,
    )
    return memo


async def poll_ton_chain(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Every 10 seconds: patrol the static address and match memo + amount."""
    transfers = await ton_payment.fetch_incoming_transfers()
    pending_reports: dict = context.application.bot_data.setdefault("pending_reports", {})

    for tx in transfers:
        if db_module.tx_already_processed(tx.tx_hash):
            continue
        payment = db_module.find_pending_memo_in_comment(tx.comment)
        if not payment:
            continue
        tier = ton_payment.classify_amount(tx.amount_ton)
        if tier is None:
            logger.info(
                "Memo matched but amount %.4f TON < 50; ignoring (tx=%s)",
                tx.amount_ton,
                tx.tx_hash,
            )
            continue

        memo = payment["payment_memo"]
        chat_id = int(payment["chat_id"])
        db_module.mark_tx_processed(tx.tx_hash, memo, chat_id, tx.amount_ton)
        db_module.confirm_payment(
            memo,
            tx_hash=tx.tx_hash,
            sender=tx.sender,
            actual_amount=tx.amount_ton,
            payment_type=tier,
        )

        extra = ""
        if tier == "single" and tx.amount_ton > SINGLE_ANALYSIS_TON:
            extra = f"\n多出 {tx.amount_ton - SINGLE_ANALYSIS_TON:.2f} TON 视为赞助，不予退还。"
        elif tier == "monthly" and tx.amount_ton > MONTHLY_SUBSCRIPTION_TON:
            extra = f"\n多出 {tx.amount_ton - MONTHLY_SUBSCRIPTION_TON:.2f} TON 视为赞助，不予退还。"

        if tier == "monthly":
            user = db_module.set_monthly_expiry(chat_id, days=30)
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"核销完成。判定：包月用户。\n"
                    f"到账 {tx.amount_ton:.2f} $TON。expiry_date = 当前时间 + 30 天"
                    f"（剩余 {user.days_remaining} 天）。无限对话已解锁。{extra}"
                ),
            )
            continue

        db_module.grant_single_unlock(chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"核销完成。判定：单次用户。\n"
                f"到账 {tx.amount_ton:.2f} $TON。深度审计权限已签发，报告交付后即刻销毁。{extra}"
            ),
        )
        payload = pending_reports.pop(chat_id, None)
        if payload:
            await _deliver_deep_audit(
                context.bot,
                chat_id,
                payload["chart"],
                payload["business_plan"],
                payload["question_key"],
                consume_single=True,
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="权限已开。现在发送生辰与商业计划，领取深度审计。\nSend /start to file the case.",
            )


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Issue a 200 TON monthly summons; memo is shared with any pending single request."""
    assert update.message is not None
    chat_id = update.message.chat_id
    user = db_module.get_or_create_user(
        chat_id,
        update.message.from_user.username or "",
        update.message.from_user.first_name or "",
    )

    if user.is_subscribed:
        await update.message.reply_text(
            f"你已经是包月用户，剩余 {user.days_remaining} 天。\n"
            f"You're already subscribed. {user.days_remaining} days remaining."
        )
        return ConversationHandler.END

    await _issue_summons(
        context.bot, chat_id, MONTHLY_SUBSCRIPTION_TON, "monthly"
    )
    return ConversationHandler.END


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None
    chat_id = update.message.chat_id
    user = db_module.get_or_create_user(chat_id, update.message.from_user.username or "", update.message.from_user.first_name or "")

    if user.is_subscribed:
        text = (
            f"📊 订阅状态 / Subscription Status\n\n"
            f"状态: ✅ 包月中 / Active\n"
            f"剩余: {user.days_remaining} 天 / days\n"
            f"累计分析: {user.analysis_count} 次\n"
            f"累计支付: {user.total_paid_ton:.2f} TON"
        )
    else:
        text = (
            f"📊 订阅状态 / Subscription Status\n\n"
            f"状态: ❌ 未订阅 / Inactive\n"
            f"累计分析: {user.analysis_count} 次\n"
            f"累计支付: {user.total_paid_ton:.2f} TON\n\n"
            f"发送 /subscribe 开通包月，自由对话不限次数。\n"
            f"Send /subscribe for unlimited monthly chat."
        )
    await update.message.reply_text(text)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.message is not None
    chat_id = update.message.chat_id
    db_module.clear_chat_history(chat_id)
    await update.message.reply_text("对话历史已清空 / Chat history cleared.")


# ─── Free Chat Mode (subscribed users) ────────────────────────────────────

async def free_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle messages from subscribed users — free-form LLM chat."""
    assert update.message is not None
    chat_id = update.message.chat_id
    user_msg = update.message.text or ""

    user = db_module.get_or_create_user(chat_id)
    if not user.is_subscribed and chat_id not in ADMIN_IDS:
        # Not subscribed — offer payment
        await update.message.reply_text(
            "未订阅用户请先支付 / Not subscribed. Please pay first:\n",
            reply_markup=_payment_keyboard(),
        )
        return

    # Save user message
    db_module.add_chat_message(chat_id, "user", user_msg)

    # Get history (last 20 messages)
    history = db_module.get_chat_history(chat_id, limit=20)
    # Remove the last user message (we just added it, will append separately)
    history = history[:-1] if history else []

    # Get birth chart from DB (persisted across sessions/restarts)
    birth_chart = user.birth_chart_text or context.user_data.get("birth_chart_text", "")

    try:
        reply = await chat_mode.chat_reply(user_msg, history, birth_chart)
    except Exception as e:
        logger.exception("Chat mode failed")
        reply = f"服务暂时不可用 / Service temporarily unavailable: {e}"

    # Save assistant reply
    db_module.add_chat_message(chat_id, "assistant", reply)

    # Send (split if too long)
    for chunk in split_telegram_text(reply):
        await update.message.reply_text(chunk)


async def payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline payment button clicks."""
    query = update.callback_query
    assert query is not None
    await query.answer()

    chat_id = query.message.chat_id if query.message else 0
    data = query.data or ""

    if data == "free_chart":
        context.user_data["mode"] = "free_chart"
        if query.message:
            await query.message.reply_text(
                "免费排盘 / Free Chart\n\n"
                "请按「YYYY-MM-DD HH:MM, 时区, 出生地」发送出生时间。\n"
                "Send birth date/time: YYYY-MM-DD HH:MM, timezone, location\n\n"
                "示例 / Example: 1992-08-15 14:30, Asia/Shanghai, 北京\n\n"
                "免费版输出：四柱 + 藏干 + 十神 + 旺衰判定。\n"
                "完整格局/用神/大运/BP交叉/生死判定需单次深度锐评或包月解锁。"
            )
        return

    elif data == "pay_single":
        await _issue_summons(context.bot, chat_id, SINGLE_ANALYSIS_TON, "single")
        if query.message:
            await query.message.reply_text(
                "单次锐评传票已签发（50.0 $TON）。备注码与包月为同一枚。\n"
                "缴税核销后，发送生辰与商业计划领取深度审计。\n\n"
                "请按「YYYY-MM-DD HH:MM, 时区, 出生地」发送出生时间。\n"
                "Example: 1992-08-15 14:30, Asia/Shanghai, 北京"
            )
        return

    elif data == "pay_subscribe":
        await _issue_summons(context.bot, chat_id, MONTHLY_SUBSCRIPTION_TON, "monthly")
        return

    elif data == "contact_admin":
        msg = (
            "没有TON？以下方式均可：\n\n"
            "📱 微信: " + ADMIN_WECHAT + "\n"
            "📨 Telegram: @BPCommentary\n"
            "💰 USDT (TRC20): 私聊客服获取地址\n\n"
            "价目表：\n"
            f"• 单次锐评: ¥350 / 50 USDT\n"
            f"• 包月对话: ¥1500 / 200 USDT/月\n\n"
            "转账后截图发客服，手动开通。\n"
            "支付宝扫码↓"
        )
        if query.message:
            await query.message.reply_text(msg)
            try:
                with open(ALIPAY_QR_PATH, "rb") as photo:
                    await context.bot.send_photo(chat_id=chat_id, photo=photo)
            except FileNotFoundError:
                await query.message.reply_text("(支付宝二维码未配置)")
        return


def main() -> None:
    require_config()

    async def post_init(app: Application) -> None:
        app.bot_data.setdefault("pending_reports", {})
        jq = app.job_queue
        if jq is None:
            logger.error(
                "JobQueue missing. Install: pip install 'python-telegram-bot[job-queue]'"
            )
            return
        jq.run_repeating(poll_ton_chain, interval=10, first=5, name="ton_patrol")
        logger.info("TonAPI chain patrol every 10s on %s", PAYMENT_WALLET)

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    conversation = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            BIRTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_birth)],
            BUSINESS_PLAN: [
                MessageHandler(
                    filters.Document.ALL | filters.PHOTO,
                    receive_business_plan_file,
                ),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_business_plan),
            ],
            QUESTION: [CallbackQueryHandler(receive_question)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    application.add_handler(conversation)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(CommandHandler("grant", admin_grant_command))
    application.add_handler(CommandHandler("grant_single", admin_grant_single_command))
    application.add_handler(CallbackQueryHandler(payment_callback, pattern="^(pay_|free_chart|contact_admin)"))
    # Free chat handler for subscribed users (must be AFTER conversation handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_chat_handler))

    logger.info("BPCommentary_bot is polling")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
