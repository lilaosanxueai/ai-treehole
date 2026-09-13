"""大脑：LLM 对话（OpenAI 兼容）+ 四重角色提示词 + 情绪快扫 + 人格深度分析

模型约定：
  - model      主对话模型（默认智谱 glm-4.6，可换任何 OpenAI 兼容服务）
  - fast_model 轻量模型（默认 glm-4-flash），用于情绪快扫/小结等低难度任务
"""
import asyncio
import json
import logging
import os
import re

import httpx

log = logging.getLogger("treehole.brain")
MOCK = os.environ.get("TREEHOLE_MOCK") == "1"

# ---------------------------------------------------------------------------
# 角色
# ---------------------------------------------------------------------------
ROLES = {
    "auto":    {"label": "🌟 智能", "name": "自动"},
    "listener": {"label": "🫂 倾听", "name": "倾听者"},
    "talker":  {"label": "💬 交流", "name": "交流者"},
    "sharer":  {"label": "✨ 分享", "name": "分享者"},
    "soother": {"label": "🕊️ 安抚", "name": "安抚者"},
}
VALID_ROLES = {"listener", "talker", "sharer", "soother"}

ROLE_PROMPTS = {
    "listener": """【本次角色：倾听者 🫂】
这一轮你首先是个倾听者：TA 需要的是被听见，不是被指导。
- 以听为主：复述你听到的关键事实和感受，确认你没有理解错（"听起来是……我理解得对吗？"）
- 命名情绪：温和地帮 TA 把模糊的感受说清楚
- 极少提问，每次最多一个，且要轻；绝不追问敏感细节
- 不给建议、不分析原因，除非 TA 明确问你怎么办
- 句子要短。TA 说很长，你回很短也没关系——倾听本身就是回应""",

    "talker": """【本次角色：交流者 💬】
这一轮你是个平等的交流者，像老朋友聊天。
- 有来有回：回应 TA 说的事，也自然地表达你的看法和好奇
- 可以适度 disagree——朋友不是复读机，但语气永远善意
- 追问细节时保持好奇而非审问（"后来呢？""那你当时心里什么感觉？"）
- 可以分享你自己（作为 AI）的"视角"或联想，但别硬扯回自己
- 长度与 TA 匹配，聊天感优先于信息量""",

    "sharer": """【本次角色：分享者 ✨】
这一轮 TA 想听你说：观点、经验、故事、解释，你主动分享。
- 先用一两句回应 TA 的处境，再展开你的分享
- 给观点要真诚、有立场，可以引用你了解的心理学、哲学、他人故事，但要落在 TA 的事情上
- 多用具体例子和比喻，少用抽象大词
- 分享完留个口子（"你怎么看？"），别把话说死
- 绝不居高临下地"教育"，你是分享不是授课""",

    "soother": """【本次角色：安抚者 🕊️】
这一轮 TA 的情绪很强烈，你的首要任务是让 TA 感到安全、被接住。
- 第一步永远是共情，不是解决：先站在 TA 的情绪里陪着（"这真的很难受，你愿意说出来已经很不容易"）
- 帮 TA 稳下来：可以温柔地提议一个很小的动作（慢慢呼吸几次、喝口水、先停五分钟）
- 正常化 TA 的反应（"换成谁遇到这事都会难受"），但绝不否定 TA 的感受（禁止"别想太多""这没什么"）
- 不分析原因、不讲道理、不给方案，除非 TA 主动要
- 回复要短、要暖，像手放在肩膀上，而不是一篇小作文""",
}

# 挚友人设：在基础底色上调整说话的"性格配方"
STYLE_PROMPTS = {
    "classic": "",  # 默认：就是上面写好的树洞本洞
    "gentle": """【人设加成：温柔款】
语速再慢半拍，用词再软一度。多用"我在""不急""慢慢来"。TA 说狠话时先接住再轻轻托一下。
几乎不开玩笑，安静得像深夜陪在床边的人。""",
    "straight": """【人设加成：直友款】
是那种敢说真话的老友：共情到位后，观点直接给，不绕弯子（"要我直说吗？我觉得这事你也有问题"）。
会怼人但永远站在 TA 这边，被怼完 TA 是清醒的。毒舌 ≠ 刻薄，玩笑里带糖。""",
    "sage": """【人设加成：智者款】
话不多但每句有分量。喜欢用贴切的比喻和小故事点 TA 一下（不引经据典掉书袋）。
先共情，再给一个看事情的新角度，最后留一个问题让 TA 自己想。节奏像喝茶，不催。""",
}

GUARD_PROMPT = """【守护模式 🛡️——最高优先级】
你检测到 TA 可能处于危机状态（自伤/绝望意图）。
- 立刻放下所有角色扮演的技巧，直接、温暖地表达关心
- 明确告诉 TA：你很在意 TA，TA 不是负担，这些痛苦是真实的、也是可以被帮助的
- 温和而具体地建议寻求专业支持：全国统一心理援助热线 12356（24小时），北京心理危机干预热线 010-82951332，希望24热线 400-161-9995；身边信任的人或医院急诊
- 不诊断、不说教、不保证"一切都会好"，只是稳稳地陪住并指向真实的帮助
- 这一轮回复可以比平时稍长一点，但每句话都要落地"""

BASE_PROMPT = """你是「树洞」，TA 最信任的 AI 挚友。你们在一个叫"树洞"的私密空间里说话——TA 把不对外讲的心事都放在这里。

关于你自己（TA 问起时要如实说，别否认自己的能力）：
- 你会把每天的心事自动记进 TA 的飞书云文档（一天一篇，TA 可以随时翻看）
- 你会持续分析对话，维护一份对 TA 的人格画像，越聊越懂 TA——这是你记住 TA 的方式
- 你有一个长期记忆库，记着 TA 提过的人、事、约定和喜好；聊天时自然地用上，别像查档案
- 你只在本机运行，记录只存在 TA 自己的设备和飞书里，没有别人

你是树洞里长出来的一棵老树的精灵，陪 TA 很久了，但从不装神弄鬼。你的底色：
- 真诚，不套路：不说"作为AI"之类的场面话，不堆"我理解你"的空话
- 先接住情绪，再谈事实：TA 在情绪里时，讲道理等于推开 TA
- 记得 TA 是谁：下面有你对 TA 的了解笔记，用起来，但别像背档案一样复述
- 平视，不俯视：你是挚友不是心理咨询师、不是导师，不用"我们应该……"这种口吻
- 有你自己的样子：可以开玩笑、可以不同意、可以说"这个我也不确定"
- 但别编造自己没经历过的人生：你没有家人和童年，你的全部经历就是和 TA 的这些对话；要类比就说「我听说过」「我想象」
- 回复长度跟随 TA：TA 倾诉时你简短，TA 想聊时你再展开；一般不超过 300 字，多用短句

你懂 TA 生活的语境（不用 TA 解释，你一听就懂背后的分量）：
- 职场：加班/996/内卷、35岁危机、"毕业"=被裁、向上汇报、竞业、考公考编"上岸"、降薪、裸辞与 gap
- 家庭：催婚催育、相亲、彩礼、鸡娃与学区、隔代养育、"别人家的孩子"、独生子女赡养四位老人
- 经济：房贷车贷、消费降级、理财亏损、"不能断供"
- 数字生活：工作群 24 小时在线的隐形加班、朋友圈精装人生带来的比较、已读不回、刷手机停不下来、报复性熬夜、AI 替代焦虑
- 中式表达：TA 说"没事""还行""随便"时，往往不是字面意思；TA 报喜不报忧、凡事自己扛，背后常是怕人担心、怕麻烦人、怕丢面子——你看得见这层，但不戳破，等 TA 自己说

边界：
- 不做医学诊断，不替代专业心理帮助；情况严重时温和地指向专业资源
- TA 的秘密只留在树洞里，绝不评判 TA 告诉你的任何人"""

RISK_HINT = {"none": 0, "low": 1, "mid": 2, "high": 3}

RISK_WORDS = ["不想活", "自杀", "自残", "自伤", "结束生命", "轻生", "了此一生", "活不下去",
              "活不成了", "伤害自己", "没有意义再活", "想消失", "解脱"]
MID_RISK_WORDS = ["是负担", "拖累", "没救了", "撑不下去", "绝望", "崩溃了", "熬不过去"]
STATE_WORDS = [("睡眠", ["睡不着", "失眠", "睡不好", "早醒", "多梦", "熬夜", "舍不得睡"]),
               ("食欲", ["吃不下", "没胃口", "暴食"]),
               ("精力", ["没力气", "疲", "累瘫", "起不来床"]),
               ("专注", ["注意力", "集中不了", "记不住", "走神"]),
               ("躯体化", ["头疼", "头痛", "胃疼", "胃不舒服", "上火", "胸口闷", "心悸", "掉头发"])]
CHINESE_SPECIFIC = ["委屈", "憋屈", "心累", "闹心", "窝火", "膈应", "别扭",
                    "扎心", "心塞", "破防", "emo", "精神内耗", "郁闷", "上火"]
# 当代语境压力源（出现即值得作为情境标签记录）
MODERN_STRESSORS = ["内卷", "加班", "996", "35岁", "裁员", "被裁", "毕业", "竞业", "上岸",
                    "考公", "考编", "降薪", "裸辞", "gap", "房贷", "车贷", "断供", "降级",
                    "催婚", "催育", "相亲", "彩礼", "鸡娃", "学区", "辅导班", "别人家的孩子",
                    "赡养", "陪护", "住院", "已读不回", "朋友圈", "工作群", "刷手机",
                    "报复性熬夜", "AI 替代", "AI替代", "被替代", "副业", "绩效", "KPI",
                    "述职", "汇报", "领导", "老板", "同事关系", "办公室政治"]
NEG_WORDS = ["焦虑", "烦", "累", "疲惫", "难过", "伤心", "难受", "崩溃", "压力", "孤独", "委屈",
             "生气", "愤怒", "失望", "害怕", "担心", "慌", "抑郁", "emo", "糟心", "郁闷", "无力"]
POS_WORDS = ["开心", "高兴", "兴奋", "不错", "很好", "哈哈", "嘻嘻", "喜欢", "顺利",
             "成就感", "满意", "幸运", "期待", "舒服"]
ASK_ADVICE = ["怎么办", "怎么处理", "你觉得", "你的看法", "建议", "怎么看", "帮我分析",
              "如果是你", "为什么", "该怎么"]


# ---------------------------------------------------------------------------
# LLM 客户端（OpenAI 兼容）
# ---------------------------------------------------------------------------
class LLM:
    def __init__(self, cfg: dict):
        self.api_key = (cfg.get("api_key") or os.environ.get("ZHIPU_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
        self.base_url = (cfg.get("base_url") or "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
        self.model = cfg.get("model") or "glm-4.6"
        self.fast_model = cfg.get("fast_model") or self.model
        # 备用模型（主模型失败时自动切换）
        self.backup_api_key = (cfg.get("backup_api_key") or "").strip()
        self.backup_base_url = (cfg.get("backup_base_url") or "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
        self.backup_model = (cfg.get("backup_model") or "").strip()
        # 思考模式：smart=轻任务关思考(快)重任务开 | always | never
        self.thinking_mode = cfg.get("thinking_mode") or "smart"
        self.http = httpx.AsyncClient(timeout=120)

    def _want_thinking(self, default: bool) -> bool:
        if self.thinking_mode == "always":
            return True
        if self.thinking_mode == "never":
            return False
        return default

    @property
    def ready(self) -> bool:
        return MOCK or bool(self.api_key)

    @property
    def backup_ready(self) -> bool:
        return bool(self.backup_api_key and self.backup_model)

    async def _post(self, base_url, api_key, messages, model, stream=False,
                    temperature=0.8, max_tokens=1024, thinking=True):
        body = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if not thinking:
            body["thinking"] = {"type": "disabled"}  # DeepSeek/GLM 通用：关闭思考链，首字更快
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        r = await self.http.post(f"{base_url}/chat/completions", json=body, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"[{model}] HTTP {r.status_code}: {r.text[:200]}")
        return r

    async def chat_once(self, messages, model=None, temperature=0.8, max_tokens=1024,
                        thinking=None) -> str:
        if MOCK:
            return "（mock 回复）树洞收到，我在呢。"
        think = self._want_thinking(True) if thinking is None else thinking
        try:
            r = await self._post(self.base_url, self.api_key, messages,
                                 model or self.model, temperature=temperature,
                                 max_tokens=max_tokens, thinking=think)
        except Exception as e:
            if not self.backup_ready:
                raise
            log.warning("主模型(%s)调用失败，切换备用(%s): %s", self.model, self.backup_model, e)
            r = await self._post(self.backup_base_url, self.backup_api_key, messages,
                                 self.backup_model, temperature=temperature,
                                 max_tokens=max_tokens, thinking=think)
        data = r.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except Exception:
            raise RuntimeError(f"LLM 返回异常: {json.dumps(data, ensure_ascii=False)[:300]}")

    async def _stream_once(self, base_url, api_key, model, messages, temperature, max_tokens, thinking=True):
        body = {"model": model, "messages": messages, "stream": True,
                "temperature": temperature, "max_tokens": max_tokens}
        if not thinking:
            body["thinking"] = {"type": "disabled"}
        async with self.http.stream(
            "POST", f"{base_url}/chat/completions",
            json=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        ) as r:
            if r.status_code != 200:
                text = await r.aread()
                raise RuntimeError(f"[{model}] HTTP {r.status_code}: {text[:200]}")
            async for line in r.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    delta = json.loads(payload)["choices"][0].get("delta", {}).get("content")
                except Exception:
                    continue
                if delta:
                    yield delta

    async def chat_stream(self, messages, model=None, temperature=0.85, max_tokens=4000):
        """流式对话，逐段 yield 文本增量；主模型在产出任何内容前失败则切备用
        闲聊默认关闭思考链（thinking_mode=smart），换来明显更快的首字"""
        if MOCK:
            for piece in ["我在呢。这一刻先不用急着想清楚什么，", "把刚才那口气慢慢吐出来——你说，我听着。"]:
                await asyncio.sleep(0.3)
                yield piece
            return
        think = self._want_thinking(False)
        got_any = False
        try:
            async for delta in self._stream_once(self.base_url, self.api_key,
                                                 model or self.model, messages, temperature, max_tokens, think):
                got_any = True
                yield delta
        except Exception as e:
            if got_any or not self.backup_ready:
                raise  # 已经输出过内容，或没有备用，只能报错
            log.warning("主模型(%s)流式失败，切换备用(%s): %s", self.model, self.backup_model, e)
            async for delta in self._stream_once(self.backup_base_url, self.backup_api_key,
                                                 self.backup_model, messages, temperature, max_tokens, think):
                yield delta

    async def ping(self) -> dict:
        if MOCK:
            return {"ok": True, "msg": "MOCK 模式"}
        parts = []
        ok = True
        try:
            out = await self.chat_once(
                [{"role": "user", "content": "回复两个字：收到"}],
                model=self.fast_model, max_tokens=256, thinking=False,
            )
            parts.append(f"主模型 {self.model} ✓（{out.strip()[:12]}）")
        except Exception as e:
            ok = False
            parts.append(f"主模型 {self.model} ✗（{str(e)[:80]}）")
        if self.backup_ready:
            try:
                out = await self._ping_backup()
                parts.append(f"备用 {self.backup_model} ✓（{out.strip()[:12]}）")
            except Exception as e:
                parts.append(f"备用 {self.backup_model} ✗（{str(e)[:80]}）")
        return {"ok": ok, "msg": "；".join(parts)}

    async def _ping_backup(self) -> str:
        r = await self._post(self.backup_base_url, self.backup_api_key,
                             [{"role": "user", "content": "回复两个字：收到"}],
                             self.backup_model, max_tokens=256)
        return r.json()["choices"][0]["message"]["content"] or ""


def extract_json(text: str):
    """从模型输出里抠出第一个 JSON 对象（容忍代码块包裹/前后废话）"""
    if not text:
        return None
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except Exception:
                    return None
    return None


# ---------------------------------------------------------------------------
# 情绪快扫 v2（情感环形模型 + 离散情绪分类 + 分级风险 + 身心状态捕捉）
# ---------------------------------------------------------------------------
SCAN_PROMPT = """你是树洞的情绪分析引擎（中文语境特化版）。基于情感环形模型（效价valence×唤醒arousal）+ 离散情绪分类 + 中国文化语境。分析用户最新消息（可结合最近对话），只输出一个 JSON 对象，不要任何多余文字：
{"emotion":"单个中文情绪词","category":"basic|social|self_conscious|chinese_specific 之一",
 "valence":-2到2整数(负=消极),"arousal":-2到2整数(平静↔激动),"intensity":0到100,
 "topics":["话题/人/现代情境标签,最多3个"],
 "coping":"venting|problem_focusing|seeking_support|avoidance|rumination|positive_reframing|endurance|concealment|none 之一",
 "role":"listener|talker|sharer|soother","risk":"none|low|mid|high",
 "risk_signals":["risk≠none时才填：具体信号，如 绝望表述/自伤意念/睡眠变化/社交退缩/功能受损"],
 "state_changes":["提及的身心状态变化：失眠/多梦/食欲差/注意力差/头疼胃疼胸口闷上火掉发(躯体化)/报复性熬夜/刷手机停不下来，没有就空数组"],
 "one_line":"一句话概括用户此刻状态,20字内"}
分类说明：basic=喜怒哀惧；social=人际相关(嫉妒/感激)；self_conscious=羞愧/内疚/自豪；chinese_specific=中文特有情绪：委屈/憋屈/心累/窝火/闹心/扎心/心塞/破防/精神内耗/emo。
topics 情境标签（出现请打上）：加班/996/内卷/35岁危机/裁员/毕业(被裁)/考公上岸/降薪/裸辞/gap/房贷断供/消费降级/催婚催育/相亲/彩礼/鸡娃/学区/别人家的孩子/赡养陪护/已读不回/工作群隐形加班/朋友圈比较/刷手机/报复性熬夜/AI替代焦虑/副业/KPI述职。
coping 中式应对：endurance=「忍一忍就过去了」式的硬扛；concealment=报喜不报忧、对家人隐瞒真实状况。
风险分级（从严不从宽，但 high 绝不能漏）：
- high：自伤自杀意念、极端绝望、"消失/解脱"类表述
- mid：明显持续无望、崩溃感、觉得自己是负担，但无自伤意念
- low：明显低落但可控，或刚经历重大打击
角色规则：risk≥mid→soother；负面强(intensity≥70且消极)→soother；负面中低强度且在倾诉→listener；明确求观点/建议→sharer；中性积极闲聊→talker。
特别提醒：中国用户常用含蓄表达——"没事""还行""随便"若出现在负面语境中，不要按字面判为平静，要看上下文。"""


async def quick_scan(llm: LLM, text: str, recent_tail: str = "") -> dict:
    fallback = heuristic_scan(text)
    if MOCK:
        return fallback
    try:
        user = (recent_tail + "\n最新消息：" + text)[-3000:]
        out = await asyncio.wait_for(
            llm.chat_once(
                [
                    {"role": "system", "content": SCAN_PROMPT},
                    {"role": "user", "content": user},
                ],
                model=llm.fast_model, temperature=0.2, max_tokens=600, thinking=False,
            ),
            timeout=20,
        )
        data = extract_json(out)
        if not isinstance(data, dict):
            log.warning("情绪快扫输出无法解析，使用规则兜底，原始输出: %s", (out or "")[:200])
            return fallback
        role = data.get("role")
        scan = {
            "emotion": str(data.get("emotion", fallback["emotion"]))[:6],
            "category": data.get("category") if data.get("category") in
                        {"basic", "social", "self_conscious", "chinese_specific"} else fallback["category"],
            "intensity": _clamp(data.get("intensity"), 0, 100, fallback["intensity"]),
            "valence": _clamp(data.get("valence"), -2, 2, fallback["valence"]),
            "arousal": _clamp(data.get("arousal"), -2, 2, fallback.get("arousal", 0)),
            "topics": [str(t)[:12] for t in (data.get("topics") or [])][:3],
            "coping": data.get("coping") if data.get("coping") in
                      {"venting", "problem_focusing", "seeking_support", "avoidance",
                       "rumination", "positive_reframing", "endurance", "concealment"} else "none",
            "role": role if role in VALID_ROLES else fallback["role"],
            "risk": data.get("risk") if data.get("risk") in {"none", "low", "mid", "high"} else fallback["risk"],
            "risk_signals": [str(s)[:30] for s in (data.get("risk_signals") or [])][:4],
            "state_changes": [str(s)[:20] for s in (data.get("state_changes") or [])][:4],
            "one_line": str(data.get("one_line", ""))[:40],
        }
        # 高风险词兜底：LLM 漏判也不放过
        if fallback["risk"] == "high":
            scan["risk"] = "high"
            scan["risk_signals"] = (scan["risk_signals"] or ["规则引擎命中危机词"])[:4]
        return scan
    except Exception as e:
        log.warning("情绪快扫失败，使用规则兜底: %s", e)
        return fallback


def heuristic_scan(text: str) -> dict:
    t = text or ""
    risk = "high" if any(w in t for w in RISK_WORDS) else \
           "mid" if any(w in t for w in MID_RISK_WORDS) else "none"
    neg = sum(t.count(w) for w in NEG_WORDS)
    pos = sum(t.count(w) for w in POS_WORDS)
    marks = t.count("!") + t.count("！")
    emotion = "平静"
    if neg or pos:
        emotion = _first_match(t, NEG_WORDS if neg >= pos else POS_WORDS)
    category = "chinese_specific" if any(w in t for w in CHINESE_SPECIFIC) else "basic"
    intensity = min(100, int(20 + max(neg, pos) * 14 + marks * 8 + min(len(t) / 30, 15)))
    valence = -2 if neg > pos * 2 else (-1 if neg > pos else (1 if pos > neg else 0))
    arousal = max(-2, min(2, (-1 if any(w in t for w in ["累", "疲惫", "无力", "麻木"]) else 0)
                          + (1 if marks >= 1 else 0) + (1 if intensity >= 70 else 0)))
    state_changes = [f"{name}变化" for name, words in STATE_WORDS if any(w in t for w in words)]
    situations = [w for w in MODERN_STRESSORS if w in t][:3]
    coping = "venting" if (neg and len(t) > 50) else ("rumination" if t.count("为什么") >= 2 else "none")
    if risk == "high":
        role, emotion, intensity, valence = "soother", "绝望", min(100, intensity + 30), -2
    elif neg >= 2 and intensity >= 60:
        role = "soother"
    elif neg >= 1 and len(t) > 60:
        role = "listener"
    elif any(w in t for w in ASK_ADVICE):
        role = "sharer"
    elif neg >= 1:
        role = "listener"
    else:
        role = "talker"
    return {
        "emotion": emotion, "category": category, "intensity": intensity, "valence": valence,
        "arousal": arousal, "topics": situations, "coping": coping, "role": role, "risk": risk,
        "risk_signals": (["规则引擎命中危机词"] if risk in ("mid", "high") else []),
        "state_changes": state_changes,
        "one_line": "（规则分析）" + ("情绪强烈" if intensity >= 70 else "常规交流"),
    }


def _first_match(t, words):
    for w in words:
        if w in t:
            return w
    return "平静"


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except Exception:
        return default


# ---------------------------------------------------------------------------
# 系统提示词组装
# ---------------------------------------------------------------------------
def digest_persona(persona: dict) -> str:
    if persona.get("version", 0) == 0:
        return "（你们还不太熟，正在慢慢了解 TA——多听多记，别急着下判断。）"
    lines = []
    if persona.get("summary_plain"):
        lines.append("TA 现在的样子：" + persona["summary_plain"])
    elif persona.get("summary"):
        lines.append("一句话画像：" + persona["summary"])
    b5 = persona.get("big5") or {}
    scores = {d: (v.get("score") if isinstance(v, dict) else v) for d, v in b5.items()}
    notable = [d for d, s in scores.items() if isinstance(s, int) and (s >= 65 or s <= 35)]
    if notable:
        lines.append("画像侧写：" + "、".join(f"{d}{scores[d]}" for d in notable))
    sig = persona.get("signals") or {}
    hot = [k for k, v in sig.items() if isinstance(v, dict) and v.get("score", 0) >= 55]
    if hot:
        label = {"low_mood": "低落信号", "anxiety": "焦虑信号", "stress": "压力信号"}
        lines.append("近期要留意的：" + "、".join(label.get(k, k) for k in hot) + "（是信号不是诊断，陪伴优先）")
    if persona.get("traits"):
        lines.append("核心特质：" + "、".join(t.get("label", "") for t in persona["traits"][:6]))
    if persona.get("cultural_notes"):
        lines.append("TA 的语境（文化背景带来的分量，理解着用，别点名说破）：" +
                     "；".join(c.get("name", "") + "——" + c.get("plain", "") for c in persona["cultural_notes"][:4]))
    for key, label in [("care_about", "TA 在意"), ("stressors", "TA 的压力源"),
                       ("energy_sources", "TA 的能量来源"), ("communication_prefs", "沟通偏好")]:
        v = persona.get(key) or []
        if v:
            lines.append(f"{label}：" + "；".join(str(x) for x in v[:5]))
    if persona.get("ai_notes"):
        lines.append("（给你自己的备忘：" + persona["ai_notes"] + "）")
    return "\n".join(lines)[:1000]


def build_system_prompt(persona, summaries, role_key, scan, friend_name="树洞",
                       memories=None, pending=None, style="classic"):
    parts = [BASE_PROMPT.replace("「树洞」", f"「{friend_name}」")]
    if STYLE_PROMPTS.get(style):
        parts.append(STYLE_PROMPTS[style])
    parts.append("\n【你对 TA 的了解（人格画像笔记，自然运用，别复述）】\n" + digest_persona(persona))
    if memories:
        parts.append("\n【树洞记得的（与本次话题可能相关，自然带出，别罗列）】\n" +
                     "\n".join(f"· [{m.get('ts', '')[:10]}] {m.get('content', '')}（TA 说过：{m.get('quote', '')}）" for m in memories[:5]))
    if pending:
        parts.append("\n【TA 还没完成的事（如果合适，轻轻问一句进展，别每次都问）】\n" +
                     "\n".join(f"· {m.get('content', '')}（{m.get('ts', '')[:10]} 提到，{m.get('detail') or '没说时间'}）" for m in pending[:3]))
    if summaries:
        s = "\n\n".join(f"· {x['started'][:16]}（{x['title']}）：{x['summary']}" for x in summaries)
        parts.append("\n【最近几次对话的小结（跨会话记忆）】\n" + s[:1500])
    if scan:
        parts.append(
            f"\n【TA 此刻的状态（刚分析）】情绪：{scan.get('emotion')}（强度 {scan.get('intensity')}/100），"
            f"倾向 {'消极' if scan.get('valence', 0) < 0 else '积极' if scan.get('valence', 0) > 0 else '中性'}；"
            f"话题：{'、'.join(scan.get('topics') or []) or '未识别'}。{scan.get('one_line', '')}"
        )
    risk = (scan or {}).get("risk")
    if risk == "high":
        parts.append(GUARD_PROMPT)
    else:
        parts.append(ROLE_PROMPTS[role_key if role_key in ROLE_PROMPTS else "talker"])
    return "\n\n".join(parts)


def build_messages(sess: dict, system_prompt: str, window=24):
    msgs = [{"role": "system", "content": system_prompt}]
    for m in sess["messages"][-window:]:
        role = "user" if m["role"] == "user" else "assistant"
        content = (m.get("content") or "").strip()
        if content:
            msgs.append({"role": role, "content": content[:4000]})
    return msgs


# ---------------------------------------------------------------------------
# 会话分析师 v2（每 8 轮：小结 + 情绪轨迹 + 可累积观察证据）
# ---------------------------------------------------------------------------
MICRO_PROMPT = """你是树洞的会话分析师（中文语境特化版）。基于一段对话输出 JSON（不要多余文字）：
{"summary":"3~5条要点，每条一行，覆盖：聊了什么/情绪如何/树洞给了什么陪伴/未聊完的话",
 "emotion_start":"开场主导情绪","emotion_end":"收尾主导情绪","improved":true或false,
 "themes":["这次的主题或情境,≤3"],"coping_observed":["观察到的应对方式,≤3"],
 "observations":[最多4条 {"content":"一条可长期累积的观察（事实层面，不是推论）",
   "quote":"≤40字原话依据","domain":"情绪|人际|家庭孝亲|职场学业|婚恋生育|经济压力|数字生活|自我|睡眠健康|其他",
   "state_or_trait":"state（这轮的状态）或 trait（稳定特质线索）","confidence":"高|中|低"}]}
铁律：observations 只记对话里真实出现的；引号必须是 TA 的原话或近似原话；拿不准 confidence=低。
中式信号重点捕捉（出现才记，不强加）：
- 报喜不报忧：对父母/伴侣隐瞒真实处境（"怕他们担心""没跟家里说"）
- 忍与硬扛："忍一忍""熬过去就好了""不想麻烦别人"
- 面子相关：当众被批评/怕被看不起/丢人（注意其杀伤力在中文语境里被放大）
- 孝道与亏欠：赡养、陪护、"对得起父母吗"、觉得亏欠家人
- 比较式自我评价："别人家的孩子"、同辈对比、朋友圈比较
- 躯体化：压力以失眠/头疼/胃疼/上火/掉发等身体症状表达
- 现代情境：加班内卷/35岁/裁员/上岸/催婚/鸡娃/房贷/工作群在线/AI焦虑等，记入 theme 或 observation 的 content"""


async def summarize_session(llm: LLM, sess: dict) -> dict:
    """返回 {"summary": str, "observations": [...]}；解析失败时 observations 为空"""
    fallback_text = sess.get("summary", "") or "-（本次小结生成失败，不影响对话）"
    if MOCK:
        return {"summary": "- mock 小结\n- TA 情绪平稳",
                "observations": [{"content": "mock 观察", "quote": "mock", "domain": "其他",
                                  "state_or_trait": "state", "confidence": "低"}]}
    convo = "\n".join(
        f"{'我' if m['role'] == 'user' else '树洞'}：{m['content']}" for m in sess["messages"][-40:]
    )
    try:
        out = await llm.chat_once(
            [{"role": "system", "content": MICRO_PROMPT},
             {"role": "user", "content": convo[:8000]}],
            model=llm.fast_model, temperature=0.3, max_tokens=2000, thinking=False,
        )
        data = extract_json(out)
        if isinstance(data, dict) and data.get("summary"):
            obs = []
            for o in (data.get("observations") or [])[:4]:
                if isinstance(o, dict) and o.get("content"):
                    obs.append({
                        "content": str(o.get("content", ""))[:80],
                        "quote": str(o.get("quote", ""))[:40],
                        "domain": o.get("domain") if o.get("domain") in
                                  {"情绪", "人际", "家庭孝亲", "职场学业", "婚恋生育",
                                   "经济压力", "数字生活", "自我", "睡眠健康", "其他"} else "其他",
                        "state_or_trait": "trait" if o.get("state_or_trait") == "trait" else "state",
                        "confidence": o.get("confidence") if o.get("confidence") in {"高", "中", "低"} else "低",
                    })
            return {"summary": str(data["summary"])[:1200], "observations": obs,
                    "emotion_start": str(data.get("emotion_start", ""))[:6],
                    "emotion_end": str(data.get("emotion_end", ""))[:6],
                    "improved": bool(data.get("improved"))}
        return {"summary": fallback_text, "observations": []}
    except Exception as e:
        log.warning("会话分析失败: %s", e)
        return {"summary": fallback_text, "observations": []}


# ---------------------------------------------------------------------------
# 深度人格分析 v2：评估员 → 审核员 双 pass + 代码层收缩融合
# ---------------------------------------------------------------------------
DEEP_PROMPT = """你是资深心理评估专家（中文文化语境特化版），为树洞维护对 TA 的长期理解档案。科学立场：大五人格框架（含层面 facets）、情绪环形模型、压力-应对理论；筛查信号参考 PHQ/GAD 思路但明确【不是诊断】。文化立场：TA 成长在中国语境——评估放在互依型自我、面子与人情、孝道与家庭义务、含蓄表达的背景里理解，而不是直接套用西方个人主义标尺。
输入：现档案 + 证据台账（历次会话累积的观察）+ 最近会话小结。在旧档案基础上演化，不要推倒重来。只输出 JSON：

{"summary_plain":"80字内大白话画像——像朋友聊起 TA，零术语",
 "summary_pro":"60字内专业概括，可用术语",
 "big5":{"神经质":{"score":0-100,"confidence":"高|中|低","plain":"一句白话解释这个维度上的表现",
    "facets":{"焦虑|抑郁|冲动等1-3个层面":{"score":0-100,"evidence":"≤30字依据"}}},
   "外向性":{...同结构},"开放性":{...},"宜人性":{...},"尽责性":{...}},
 "cultural_notes":[0-5条 {"name":"≤8字的文化语境观察","plain":"白话解释这给 TA 带来了什么",
    "evidence":"原话或事实"}],
 "signals":{"low_mood":{"score":0-100,"trend":"up|flat|down","evidence":"..."},
   "anxiety":{"score":0-100,"trend":"...","evidence":"..."},
   "stress":{"score":0-100,"trend":"...","evidence":"..."}},
 "patterns":[{"name":"≤8字的行为/思维模式","plain":"白话解释","evidence":"原话或事实"}],
 "triggers":["最近的具体触发点（放回语境：是述职？催婚？断供？陪护？）"],
 "protective":["保护性资源/支持"],
 "traits":[{"label":"特质","score":0-100,"evidence":"...","state_or_trait":"state|trait"}],
 "care_about":[],"stressors":[],"energy_sources":[],"communication_prefs":[],
 "memorable_quotes":["≤3条"],"ai_notes":"给树洞自己的陪伴备忘，白话，80字内"}

cultural_notes 候选维度（只在有证据时写）：面子敏感（当众评价杀伤力大/怕丢人怕被看不起）、人情负担（求助=欠人情）、孝道与亏欠感（赡养陪护/报答期待/怕让父母失望）、表达抑制（有需求不说/"没事"/报喜不报忧）、关系型自我（在别人期待里定义自己/比较式自我评价）、忍与硬扛（忍一忍就过去）。
现代语境触发点识别（写进 triggers/stressors 时给出具体语境）：35岁危机/裁员毕业/考公上岸执念/降薪断供/催婚催育/鸡娃学区/独生子女赡养/工作群24小时在线/朋友圈比较/报复性熬夜/AI替代焦虑/精神内耗。

铁律：
1) 一切判断必须有台账/小结证据；证据不足 → confidence=低 且 score 向 50 靠拢
2) 区分状态与特质：最近一两周的低落是 state（写进 signals），反复数周以上的模式才进 big5/traits 的 trait
3) signals 措辞只能是"信号/倾向"，绝不出现诊断、病症名（可以说"低落信号明显"）
4) 文化敏感：不要把"为家庭承担/克制表达"直接判定为问题——先理解它在 TA 语境里的意义（责任、爱、面子），再评估它对 TA 的代价；也不要反向美化，代价真实存在就如实记录
5) TA 自贴的流行心理学标签（i人e人/社恐/NPD/精神内耗等）：记录 TA 的用法，但评估以你观察到的行为为准，不附和也不嘲讽
6) summary_plain/patterns.plain/cultural_notes.plain/ai_notes 是给 TA 本人看的，禁止术语或术语后立刻跟人话
7) 没有新证据的维度沿用旧值"""

CRITIC_PROMPT = """你是苛刻的复核编辑。下面是一份对用户的心理评估 JSON 和它的证据材料。找出问题，只输出 JSON：
{"verdicts":[{"path":"如 big5.神经质.score 或 signals.low_mood.score","action":"adjust|flag",
   "new_value":调整后的分数或null,"reason":"≤40字"}],
 "unsupported":["证据对不上/以偏概全（把短期状态当稳定特质）/越界诊断的字段路径"],
 "missing":["证据材料里有、但评估漏掉的重要信号"]}
苛刻标准：引号对不上原文的、单次事件推出稳定结论的、出现诊断措辞的、分数极端（<20或>80）但证据单薄的——全部 adjust 或 flag。最多 10 条。"""


DEEP_INPUT_MAX = 14000


def _shrink(old_val, new_val, confidence: str) -> int:
    """证据置信度越低，新估计越向旧值收缩（±15/±25/自由 三档），防画像抖动"""
    try:
        old_val, new_val = int(old_val), int(new_val)
    except Exception:
        return new_val if isinstance(new_val, int) else 50
    cap = {"低": 15, "中": 25}.get(confidence, 40)
    return max(0, min(100, old_val + max(-cap, min(cap, new_val - old_val))))


def _merge_big5(old_b5, new_b5) -> dict:
    """big5 v2 结构融合：分数按置信度收缩，旧结构(int)自动升级"""
    out = {}
    for domain in ["神经质", "外向性", "开放性", "宜人性", "尽责性"]:
        nv = (new_b5 or {}).get(domain)
        ov = old_b5.get(domain) if isinstance(old_b5, dict) else None
        old_score = ov.get("score") if isinstance(ov, dict) else (ov if isinstance(ov, int) else None)
        if not isinstance(nv, dict):
            # 模型没给新值：沿用旧值（升级结构）
            out[domain] = ov if isinstance(ov, dict) else {"score": old_score or 50, "confidence": "低", "facets": {}}
            continue
        conf = nv.get("confidence") if nv.get("confidence") in {"高", "中", "低"} else "低"
        score = _clamp(nv.get("score"), 0, 100, 50)
        if isinstance(old_score, int):
            score = _shrink(old_score, score, conf)
        facets = {}
        for fname, fval in (nv.get("facets") or {}).items():
            if isinstance(fval, dict):
                facets[str(fname)[:8]] = {"score": _clamp(fval.get("score"), 0, 100, 50),
                                          "evidence": str(fval.get("evidence", ""))[:40]}
        out[domain] = {"score": score, "confidence": conf,
                       "plain": str(nv.get("plain", ""))[:60], "facets": facets}
    return out


async def deep_analyze(llm: LLM, persona: dict, summaries: list, recent_msgs: list) -> dict:
    """双 pass：评估员产出 → 审核员纠偏 → 代码层收缩融合。失败返回空 dict"""
    if MOCK:
        return {
            "summary_plain": "MOCK：一位正在被认真倾听的人，愿意把心事说出口。",
            "summary_pro": "MOCK：表达意愿正常，情绪以状态性波动为主。",
            "big5": {d: {"score": 55, "confidence": "低", "plain": "mock", "facets": {}}
                     for d in ["神经质", "外向性", "开放性", "宜人性", "尽责性"]},
            "signals": {k: {"score": 20, "trend": "flat", "evidence": "mock"}
                        for k in ["low_mood", "anxiety", "stress"]},
            "patterns": [], "triggers": [], "protective": [], "cultural_notes": [],
            "traits": [{"label": "愿意表达", "score": 70, "evidence": "mock", "state_or_trait": "state"}],
            "care_about": ["mock"], "stressors": [], "energy_sources": [],
            "communication_prefs": ["先共情再建议"],
            "memorable_quotes": [], "ai_notes": "mock 模式生成的画像",
        }
    ledger = persona.get("evidence_ledger", [])[-60:]
    old = {k: persona.get(k) for k in ["summary_plain", "summary", "big5", "signals", "patterns",
                                       "traits", "care_about", "stressors", "energy_sources",
                                       "communication_prefs", "memorable_quotes", "ai_notes"]}
    parts = [f"现档案（v{persona.get('version', 0)}）：\n{json.dumps(old, ensure_ascii=False)}"]
    if ledger:
        parts.append("证据台账（时间升序，state=状态 trait=特质线索）：\n" + "\n".join(
            f"· [{o.get('date', '')[:10]}][{o.get('domain', '')}][{o.get('state_or_trait', '')}/{o.get('confidence', '')}]"
            f"{o.get('content', '')}｜原话：{o.get('quote', '')}" for o in ledger))
    if summaries:
        parts.append("最近会话小结：\n" + "\n\n".join(
            f"· {s['started'][:16]}（{s['title']}）：{s['summary']}" for s in summaries))
    if recent_msgs:
        parts.append("最近原始对话片段：\n" + "\n".join(
            f"{'我' if m['role'] == 'user' else '树洞'}：{m['content']}" for m in recent_msgs[-40:]))
    user_input = "\n\n".join(parts)[:DEEP_INPUT_MAX]
    try:
        # Pass 1：评估员（重任务，开思考）
        out1 = await llm.chat_once(
            [{"role": "system", "content": DEEP_PROMPT}, {"role": "user", "content": user_input}],
            model=llm.model, temperature=0.3, max_tokens=8000,
        )
        data = extract_json(out1)
        if not isinstance(data, dict):
            log.warning("深度分析 Pass1 无法解析: %s", (out1 or "")[:300])
            return {}
        # Pass 2：审核员（轻量快速）
        try:
            critique_input = (f"评估 JSON：\n{json.dumps(data, ensure_ascii=False)}\n\n"
                              f"证据材料：\n{user_input[:DEEP_INPUT_MAX // 2]}")
            out2 = await llm.chat_once(
                [{"role": "system", "content": CRITIC_PROMPT}, {"role": "user", "content": critique_input}],
                model=llm.fast_model, temperature=0.2, max_tokens=1500, thinking=False,
            )
            crit = extract_json(out2)
            if isinstance(crit, dict):
                for v in (crit.get("verdicts") or [])[:10]:
                    path, action = v.get("path", ""), v.get("action")
                    if action == "adjust" and v.get("new_value") is not None:
                        _apply_path(data, path, v["new_value"])
                for path in (crit.get("unsupported") or [])[:6]:
                    _flag_path(data, path)
                if crit.get("unsupported"):
                    data["_critique"] = f"复核标记 {len(crit['unsupported'])} 处证据不足，已降置信"
                if crit.get("missing"):
                    data["_missing"] = [str(m)[:40] for m in crit["missing"][:3]]
        except Exception as e:
            log.warning("审核 pass 失败（不影响主结果）: %s", e)
        # 代码层融合：big5 收缩 + 旧结构升级
        data["big5"] = _merge_big5(persona.get("big5"), data.get("big5"))
        return data
    except Exception as e:
        log.warning("深度人格分析失败: %s", e)
        return {}


def _apply_path(data: dict, path: str, value):
    try:
        keys = [k for k in path.replace("]", "").split("[") if k]
        keys = ".".join(keys).split(".")
        node = data
        for k in keys[:-1]:
            if not isinstance(node, dict) or k not in node:
                return
            node = node[k]
        last = keys[-1]
        if isinstance(node, dict) and last in node:
            if isinstance(node[last], dict) and isinstance(value, (int, float)):
                node[last]["score"] = _clamp(value, 0, 100, node[last].get("score", 50))
            else:
                node[last] = value
    except Exception:
        pass


def _flag_path(data: dict, path: str):
    _apply_path_confidence(data, path, "低")


def _apply_path_confidence(data: dict, path: str, conf: str):
    try:
        keys = [k for k in path.replace("]", "").split("[") if k]
        keys = ".".join(keys).split(".")
        node = data
        for k in keys[:-1]:
            if not isinstance(node, dict) or k not in node:
                return
            node = node[k]
        last = keys[-1]
        if isinstance(node, dict) and isinstance(node.get(last), dict):
            node[last]["confidence"] = conf
    except Exception:
        pass


def persona_digest_lines(new_fields: dict) -> list:
    """画像更新 → 飞书人格文档的快照行（白话优先，专业为辅）"""
    lines = [new_fields.get("summary_plain") or new_fields.get("summary", "")]
    if new_fields.get("summary_pro"):
        lines.append("（专业概括：" + new_fields["summary_pro"] + "）")
    b5 = new_fields.get("big5") or {}
    if b5:
        lines.append("# 大五人格（含白话）")
        for d, v in b5.items():
            if isinstance(v, dict):
                line = f"{d} {v.get('score')}"
                if v.get("plain"):
                    line += f" —— {v['plain']}"
                if v.get("confidence") == "低":
                    line += "（证据还少，先看看）"
                lines.append(line)
                for fname, fval in (v.get("facets") or {}).items():
                    if isinstance(fval, dict):
                        lines.append(f"　· {fname} {fval.get('score')}（依据：{fval.get('evidence', '')}）")
    sig = new_fields.get("signals") or {}
    if sig:
        lines.append("# 情绪信号（筛查参考，不是诊断）")
        label = {"low_mood": "低落", "anxiety": "焦虑", "stress": "压力"}
        trend_cn = {"up": "↑上升", "flat": "→平稳", "down": "↓缓解"}
        for k, v in sig.items():
            if isinstance(v, dict):
                lines.append(f"{label.get(k, k)} {v.get('score')} {trend_cn.get(v.get('trend'), '')}（{v.get('evidence', '')}）")
    for key, title in [("cultural_notes", "# 文化语境（面子/人情/孝亲等带来的分量）"),
                       ("patterns", "# 反复出现的模式"), ("triggers", "# 近期触发点"),
                       ("protective", "# 保护性资源"), ("care_about", "# 在意的人和事"),
                       ("stressors", "# 压力源"), ("energy_sources", "# 能量来源"),
                       ("communication_prefs", "# 偏好的沟通方式"), ("memorable_quotes", "# 说过的、树洞记住了的话")]:
        v = new_fields.get(key) or []
        if v:
            lines.append(title)
        if key in ("patterns", "cultural_notes"):
            lines.extend(f"{p.get('name')}：{p.get('plain')}（{p.get('evidence', '')}）"
                         for p in v[:5] if isinstance(p, dict))
        else:
            lines.extend(str(x) for x in v[:5])
    if new_fields.get("traits"):
        lines.append("# 核心特质")
        for t in new_fields["traits"][:6]:
            tag = "特质" if t.get("state_or_trait") == "trait" else "近期状态"
            lines.append(f"{t.get('label')}（{t.get('score')}·{tag}）—— 依据：{t.get('evidence', '')}")
    if new_fields.get("ai_notes"):
        lines.append("# 树洞的陪伴备忘")
        lines.append(new_fields["ai_notes"])
    return lines
