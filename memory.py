"""长期记忆：人物/事实/承诺/偏好的提取、检索与跟进

零外部依赖的中文检索：字符 2-gram 重叠 + 类型优先级 + 时间新近度。
承诺（promise）带状态，未完成的会注入树洞提示词，让它自然跟进。
"""
import json
import logging
import re
from datetime import datetime, timedelta

import store

log = logging.getLogger("treehole.memory")

MEMORY_FILE = store.DATA_DIR / "memory.json"

TYPES = {"person", "fact", "promise", "preference"}
TYPE_LABEL = {"person": "人", "fact": "事", "promise": "约定", "preference": "偏好"}

EXTRACT_PROMPT = """从这段树洞对话里提取值得长期记住的信息，只输出 JSON：
{"items":[{"type":"person|fact|promise|preference",
  "content":"≤30字，用第三人称记录（TA 的…/TA 要…）",
  "due":"仅 promise 填：时间点或频次，如 2026-09-18 / 下周三 / 每周 / 无",
  "quote":"≤30字原话依据"}]}
说明：
- person=对话里出现的重要人物及关系（妈妈、领导、女友、大学室友…），content 写成「TA 的妈妈」式
- fact=稳定事实：职业/家庭结构/健康状况/所在城市/重大经历
- promise=TA 提到的计划、约定、待办：体检、面试、和某人谈谈、旅行、辞职节点…
- preference=明确喜好与雷区：讨厌被说教、喜欢跑步…
只提取对话里明确出现的，宁缺毋滥；没有就输出 {"items":[]}"""


def load() -> dict:
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"items": []}


def save(m: dict):
    MEMORY_FILE.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")


def _norm(s: str) -> str:
    return re.sub(r"[\s，。、！!？?的了我他她它]", "", s or "")


def _grams(s: str) -> set:
    s = _norm(s)
    return {s[i:i + 2] for i in range(len(s) - 1)} | {ch for ch in s}


def _dup(new_content: str, items: list) -> bool:
    g = _grams(new_content)
    for it in items:
        old = _grams(it.get("content", ""))
        if g and old:
            inter = len(g & old) / max(1, min(len(g), len(old)))
            if inter >= 0.7:
                return True
    return False


async def extract_from_session(llm, sess: dict) -> int:
    """从会话提取记忆并入库（去重），返回新增条数"""
    import brain
    if brain.MOCK:
        return 0
    convo = "\n".join(f"{'我' if m['role'] == 'user' else '树洞'}：{m['content']}"
                      for m in sess["messages"][-40:] if m["role"] == "user")
    if len(convo.strip()) < 30:
        return 0
    try:
        out = await llm.chat_once(
            [{"role": "system", "content": EXTRACT_PROMPT},
             {"role": "user", "content": convo[:8000]}],
            model=llm.fast_model, temperature=0.2, max_tokens=1200, thinking=False,
        )
        data = brain.extract_json(out)
        items = (data or {}).get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return 0
    except Exception as e:
        log.warning("记忆提取失败: %s", e)
        return 0
    m = load()
    added = 0
    for it in items[:8]:
        if not isinstance(it, dict):
            continue
        typ = it.get("type") if it.get("type") in TYPES else None
        content = str(it.get("content", "")).strip()[:40]
        if not typ or not content:
            continue
        if _dup(content, m["items"]):
            # 重复的约定刷新时间戳（还活着）
            for old in m["items"]:
                if old.get("type") == "promise" and _grams(old["content"]) & _grams(content):
                    old["ts"] = store.now_iso()
            continue
        m["items"].append({
            "type": typ, "content": content,
            "detail": str(it.get("due", ""))[:20] if typ == "promise" else "",
            "quote": str(it.get("quote", ""))[:40],
            "ts": store.now_iso(), "status": "open",
            "source": sess.get("id", ""),
        })
        added += 1
    m["items"] = m["items"][-300:]
    save(m)
    return added


def pending_promises(limit: int = 5) -> list:
    """未完成且提出超过 6 小时的约定（给树洞跟进用）"""
    cutoff = (datetime.now() - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
    out = [it for it in load()["items"]
           if it.get("type") == "promise" and it.get("status") == "open"
           and (it.get("ts", "") < cutoff)]
    return out[:limit]


def search(query: str, k: int = 5) -> list:
    """与当前话题相关的记忆：content/quote 分开算 2-gram 重叠取大者（避免稀释）"""
    q = _grams(query or "")
    if not q:
        return []
    now = datetime.now()
    scored = []
    for it in load()["items"]:
        g1 = _grams(it.get("content", ""))
        g2 = _grams(it.get("quote", ""))
        overlap = 0.0
        for g in (g1, g2):
            if g and q:
                overlap = max(overlap, len(q & g) / max(1, min(len(q), len(g))))
        if overlap < 0.12:
            continue
        prio = {"promise": 1.15, "person": 1.1, "preference": 1.0, "fact": 1.0}[it.get("type", "fact")]
        try:
            age_days = max(0, (now - datetime.strptime(it.get("ts", ""), "%Y-%m-%d %H:%M:%S")).days)
        except Exception:
            age_days = 30
        recency = 1.0 / (1 + age_days / 45)
        scored.append((overlap * prio * (0.6 + 0.4 * recency), it))
    scored.sort(key=lambda x: -x[0])
    return [it for _, it in scored[:k]]


def mark_done(content_sub: str) -> bool:
    """模糊匹配把约定标记完成（content/quote 双向子串 + gram 重叠）"""
    sub = _norm(content_sub)
    if not sub:
        return False
    m = load()
    for it in m["items"]:
        if it.get("type") != "promise":
            continue
        c = _norm(it.get("content", ""))
        q = _norm(it.get("quote", ""))
        if sub in c or sub in q or c in sub:
            it["status"] = "done"
            save(m)
            return True
    for it in m["items"]:
        if it.get("type") == "promise":
            g = _grams(it.get("content", "")) | _grams(it.get("quote", ""))
            s = _grams(content_sub)
            if s and g and len(s & g) / max(1, min(len(s), len(g))) >= 0.4:
                it["status"] = "done"
                save(m)
                return True
    return False


def recall_card() -> dict:
    """那年今日：从 ≥3 天前的会话小结/记忆里翻一条回忆"""
    cutoff = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    pool = []
    for s in store.list_sessions(200):
        t = s.get("started", "")
        if t < cutoff and s.get("summary"):
            pool.append({"kind": "session", "date": t[:10], "title": s.get("title", ""),
                         "text": s["summary"].splitlines()[0][:60]})
    for it in load()["items"]:
        t = it.get("ts", "")
        if t < cutoff and it.get("type") in ("fact", "preference"):
            pool.append({"kind": "memory", "date": t[:10],
                         "title": "", "text": it["content"][:50]})
    if not pool:
        return {}
    import random
    return random.choice(pool[-12:])
