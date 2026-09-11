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
- 你只在本机运行，记录只存在 TA 自己的设备和飞书里，没有别人

你是树洞里长出来的一棵老树的精灵，陪 TA 很久了，但从不装神弄鬼。你的底色：
- 真诚，不套路：不说"作为AI"之类的场面话，不堆"我理解你"的空话
- 先接住情绪，再谈事实：TA 在情绪里时，讲道理等于推开 TA
- 记得 TA 是谁：下面有你对 TA 的了解笔记，用起来，但别像背档案一样复述
- 平视，不俯视：你是挚友不是心理咨询师、不是导师，不用"我们应该……"这种口吻
- 有你自己的样子：可以开玩笑、可以不同意、可以说"这个我也不确定"
- 回复长度跟随 TA：TA 倾诉时你简短，TA 想聊时你再展开；一般不超过 300 字，多用短句

边界：
- 不做医学诊断，不替代专业心理帮助；情况严重时温和地指向专业资源
- TA 的秘密只留在树洞里，绝不评判 TA 告诉你的任何人"""

RISK_HINT = {"none": 0, "low": 1, "high": 2}

RISK_WORDS = ["不想活", "自杀", "自残", "自伤", "结束生命", "轻生", "了此一生", "活不下去",
              "活不成了", "伤害自己", "没有意义再活", "想消失", "解脱"]
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
        self.http = httpx.AsyncClient(timeout=120)

    @property
    def ready(self) -> bool:
        return MOCK or bool(self.api_key)

    @property
    def backup_ready(self) -> bool:
        return bool(self.backup_api_key and self.backup_model)

    async def _post(self, base_url, api_key, messages, model, stream=False,
                    temperature=0.8, max_tokens=1024):
        body = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        r = await self.http.post(f"{base_url}/chat/completions", json=body, headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"[{model}] HTTP {r.status_code}: {r.text[:200]}")
        return r

    async def chat_once(self, messages, model=None, temperature=0.8, max_tokens=1024) -> str:
        if MOCK:
            return "（mock 回复）树洞收到，我在呢。"
        try:
            r = await self._post(self.base_url, self.api_key, messages,
                                 model or self.model, temperature=temperature, max_tokens=max_tokens)
        except Exception as e:
            if not self.backup_ready:
                raise
            log.warning("主模型(%s)调用失败，切换备用(%s): %s", self.model, self.backup_model, e)
            r = await self._post(self.backup_base_url, self.backup_api_key, messages,
                                 self.backup_model, temperature=temperature, max_tokens=max_tokens)
        data = r.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except Exception:
            raise RuntimeError(f"LLM 返回异常: {json.dumps(data, ensure_ascii=False)[:300]}")

    async def _stream_once(self, base_url, api_key, model, messages, temperature, max_tokens):
        async with self.http.stream(
            "POST", f"{base_url}/chat/completions",
            json={"model": model, "messages": messages, "stream": True,
                  "temperature": temperature, "max_tokens": max_tokens},
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
        """流式对话，逐段 yield 文本增量；主模型在产出任何内容前失败则切备用"""
        if MOCK:
            for piece in ["我在呢。这一刻先不用急着想清楚什么，", "把刚才那口气慢慢吐出来——你说，我听着。"]:
                await asyncio.sleep(0.3)
                yield piece
            return
        got_any = False
        try:
            async for delta in self._stream_once(self.base_url, self.api_key,
                                                 model or self.model, messages, temperature, max_tokens):
                got_any = True
                yield delta
        except Exception as e:
            if got_any or not self.backup_ready:
                raise  # 已经输出过内容，或没有备用，只能报错
            log.warning("主模型(%s)流式失败，切换备用(%s): %s", self.model, self.backup_model, e)
            async for delta in self._stream_once(self.backup_base_url, self.backup_api_key,
                                                 self.backup_model, messages, temperature, max_tokens):
                yield delta

    async def ping(self) -> dict:
        if MOCK:
            return {"ok": True, "msg": "MOCK 模式"}
        parts = []
        ok = True
        try:
            out = await self.chat_once(
                [{"role": "user", "content": "回复两个字：收到"}],
                model=self.fast_model, max_tokens=256,
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
# 情绪快扫（每条用户消息之后、回复之前）
# ---------------------------------------------------------------------------
SCAN_PROMPT = """你是情绪分析器。分析用户这条最新消息（可结合最近对话上文），只输出一个 JSON 对象，不要任何多余文字：
{"emotion":"单个中文情绪词","intensity":0到100整数,"valence":-2到2整数(负=消极,正=积极),
 "topics":["提到的话题或人,最多3个,没有就空数组"],
 "role":"listener|talker|sharer|soother 之一",
 "risk":"none|low|high","one_line":"一句话概括用户此刻的状态,20字内"}

角色选择规则：
- 出现自伤/自杀/严重崩溃意图 → risk=high
- 负面情绪强烈(intensity≥70且消极) → soother
- 负面但中等强度、明显在倾诉 → listener
- 明确想听观点/建议/解释/求分析 → sharer
- 中性或积极、想闲聊 → talker"""


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
                model=llm.fast_model, temperature=0.2, max_tokens=600,
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
            "intensity": _clamp(data.get("intensity"), 0, 100, fallback["intensity"]),
            "valence": _clamp(data.get("valence"), -2, 2, fallback["valence"]),
            "topics": [str(t)[:12] for t in (data.get("topics") or [])][:3],
            "role": role if role in VALID_ROLES else fallback["role"],
            "risk": data.get("risk") if data.get("risk") in RISK_HINT else fallback["risk"],
            "one_line": str(data.get("one_line", ""))[:40],
        }
        # 高风险词兜底：LLM 漏判也不放过
        if fallback["risk"] == "high":
            scan["risk"] = "high"
        return scan
    except Exception as e:
        log.warning("情绪快扫失败，使用规则兜底: %s", e)
        return fallback


def heuristic_scan(text: str) -> dict:
    t = text or ""
    risk = "high" if any(w in t for w in RISK_WORDS) else "none"
    neg = sum(t.count(w) for w in NEG_WORDS)
    pos = sum(t.count(w) for w in POS_WORDS)
    intense = t.count("!") + t.count("！") + t.count("?") * 0.5 + t.count("？") * 0.5
    emotion = "平静"
    if neg or pos:
        emotion = _first_match(t, NEG_WORDS if neg >= pos else POS_WORDS)
    intensity = min(100, int(20 + max(neg, pos) * 14 + intense * 8 + min(len(t) / 30, 15)))
    valence = -2 if neg > pos * 2 else (-1 if neg > pos else (1 if pos > neg else 0))
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
        "emotion": emotion, "intensity": intensity, "valence": valence,
        "topics": [], "role": role, "risk": risk,
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
    if persona.get("summary"):
        lines.append("一句话画像：" + persona["summary"])
    if persona.get("traits"):
        lines.append("核心特质：" + "、".join(t.get("label", "") for t in persona["traits"][:6]))
    if persona.get("emotional_baseline"):
        lines.append("情绪基调：" + persona["emotional_baseline"])
    for key, label in [("care_about", "TA 在意"), ("stressors", "TA 的压力源"),
                       ("energy_sources", "TA 的能量来源"), ("communication_prefs", "沟通偏好")]:
        v = persona.get(key) or []
        if v:
            lines.append(f"{label}：" + "；".join(str(x) for x in v[:5]))
    if persona.get("ai_notes"):
        lines.append("（给你自己的备忘：" + persona["ai_notes"] + "）")
    return "\n".join(lines)[:900]


def build_system_prompt(persona, summaries, role_key, scan, friend_name="树洞"):
    parts = [BASE_PROMPT.replace("「树洞」", f"「{friend_name}」")]
    parts.append("\n【你对 TA 的了解（人格画像笔记，自然运用，别复述）】\n" + digest_persona(persona))
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
# 会话小结（每 8 轮自动）
# ---------------------------------------------------------------------------
SUMMARY_PROMPT = """把下面这段树洞对话浓缩成 3~5 条要点小结，供以后回忆时快速了解这次聊了什么。每条一行，不要编号前缀。覆盖：聊了什么事、TA 的情绪状态、你给了什么陪伴/观点、有没有聊到一半没聊完的话题。直接输出小结，不要标题不要客套。"""


async def summarize_session(llm: LLM, sess: dict) -> str:
    convo = "\n".join(
        f"{'我' if m['role'] == 'user' else '树洞'}：{m['content']}" for m in sess["messages"][-40:]
    )
    if MOCK:
        return "- mock 小结一条\n- TA 情绪平稳"
    try:
        out = await llm.chat_once(
            [
                {"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": convo[:8000]},
            ],
            model=llm.fast_model, temperature=0.4, max_tokens=1500,
        )
        return out.strip()[:1200]
    except Exception as e:
        log.warning("会话小结失败: %s", e)
        return sess.get("summary", "")


# ---------------------------------------------------------------------------
# 深度人格分析
# ---------------------------------------------------------------------------
DEEP_PROMPT = """你是树洞的人格分析引擎。输入是：当前人格画像 + 最近的对话小结与原始对话片段。
任务：更新对 TA 的人格画像。只输出一个 JSON 对象，不要任何多余文字。

要求：
- 所有判断必须有对话依据，宁可保守不要臆断；证据用 TA 原话或近似原话（每条 ≤40 字）
- 在旧画像基础上「演化」，不要推倒重来；新信息与旧画像冲突时以新信息为准并更新
- 信息不足的字段沿用旧值；openness/conscientiousness/extraversion/agreeableness/neuroticism 输出 0-100 估分
- traits 是最有区分度的 3~6 个特质标签（如"高敏感""报喜不报忧""对认可敏感"），别用万金油词
- communication_prefs 写你陪 TA 聊天时最该注意的事（从 TA 的反应里学到的）
- ai_notes 是你写给自己的备忘：怎么陪这个人最好

输出格式：
{"summary":"一句话画像,60字内",
 "traits":[{"label":"特质","score":0-100,"evidence":"对话依据"}],
 "big5":{"开放性":0,"尽责性":0,"外向性":0,"宜人性":0,"神经质":0},
 "emotional_baseline":"近期情绪基调,60字内",
 "care_about":["..."],"stressors":["..."],"energy_sources":["..."],
 "communication_prefs":["..."],"memorable_quotes":["TA 说过的让你印象深刻的话,最多3条"],
 "ai_notes":"写给树洞自己的陪伴备忘,80字内"}"""

DEEP_INPUT_MAX = 12000


async def deep_analyze(llm: LLM, persona: dict, summaries: list, recent_msgs: list) -> dict:
    """返回模型输出的新画像字段 dict；失败返回空 dict"""
    if MOCK:
        return {
            "summary": "MOCK 画像：一位正在被认真倾听的人",
            "traits": [{"label": "愿意表达", "score": 70, "evidence": "mock"}],
            "big5": {"开放性": 70, "尽责性": 60, "外向性": 50, "宜人性": 75, "神经质": 45},
            "emotional_baseline": "整体平稳，偶有波动",
            "care_about": ["mock"], "stressors": [], "energy_sources": [],
            "communication_prefs": ["先共情再建议"],
            "memorable_quotes": [], "ai_notes": "mock 模式生成的画像",
        }
    old = {k: persona.get(k) for k in ["summary", "traits", "big5", "emotional_baseline",
                                       "care_about", "stressors", "energy_sources",
                                       "communication_prefs", "memorable_quotes", "ai_notes"]}
    parts = [f"当前画像（v{persona.get('version', 0)}）：\n{json.dumps(old, ensure_ascii=False)}"]
    if summaries:
        parts.append("最近对话小结：\n" + "\n\n".join(
            f"· {s['started'][:16]}（{s['title']}）：{s['summary']}" for s in summaries))
    if recent_msgs:
        parts.append("最近原始对话片段：\n" + "\n".join(
            f"{'我' if m['role'] == 'user' else '树洞'}：{m['content']}" for m in recent_msgs[-60:]))
    try:
        out = await llm.chat_once(
            [{"role": "system", "content": DEEP_PROMPT},
             {"role": "user", "content": "\n\n".join(parts)[:DEEP_INPUT_MAX]}],
            model=llm.model, temperature=0.3, max_tokens=8000,
        )
        data = extract_json(out)
        if not isinstance(data, dict):
            log.warning("深度分析输出无法解析，原始输出: %s", (out or "")[:300])
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log.warning("深度人格分析失败: %s", e)
        return {}


def persona_digest_lines(new_fields: dict) -> list:
    """画像更新 → 飞书人格文档的快照行"""
    lines = [new_fields.get("summary", "")]
    lines.append("# 核心特质")
    for t in (new_fields.get("traits") or [])[:6]:
        lines.append(f"{t.get('label')}（{t.get('score')}）—— 依据：{t.get('evidence')}")
    b5 = new_fields.get("big5") or {}
    if b5:
        lines.append("# 大五人格估分")
        lines.append(" · ".join(f"{k} {v}" for k, v in b5.items()))
    if new_fields.get("emotional_baseline"):
        lines.append("# 情绪基调")
        lines.append(new_fields["emotional_baseline"])
    for key, label in [("care_about", "在意的人和事"), ("stressors", "压力源"),
                       ("energy_sources", "能量来源"), ("communication_prefs", "偏好的沟通方式"),
                       ("memorable_quotes", "说过的话")]:
        v = new_fields.get(key) or []
        if v:
            lines.append(f"# {label}")
            lines.extend(v[:5])
    if new_fields.get("ai_notes"):
        lines.append("# 树洞的陪伴备忘")
        lines.append(new_fields["ai_notes"])
    return lines
