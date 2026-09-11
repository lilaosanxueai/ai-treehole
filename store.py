"""本地存储：会话记录 + 人格模型 + 运行状态

所有数据都落在本机 data/ 目录（已被 .gitignore 排除），飞书云文档只是同步副本。
"""
import json
import time
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SESSIONS_DIR = DATA_DIR / "sessions"

PERSONA_FILE = DATA_DIR / "persona.json"
STATE_FILE = DATA_DIR / "state.json"
PENDING_FEISHU = DATA_DIR / "pending_feishu.json"

WEEKDAYS = "一二三四五六日"


def now_ts() -> float:
    return time.time()


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def today_label() -> str:
    d = datetime.now()
    return f'{d.strftime("%m-%d")} 周{WEEKDAYS[d.weekday()]}'


def _ensure_dirs():
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


_ensure_dirs()


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
def _session_path(sid: str) -> Path:
    return SESSIONS_DIR / f"{sid}.json"


def new_session() -> dict:
    sid = datetime.now().strftime("%Y%m%d") + "_" + uuid.uuid4().hex[:8]
    sess = {
        "id": sid,
        "title": "",
        "started": now_iso(),
        "messages": [],   # [{role, content, ts, scan?}]
        "turns": 0,
        "summary": "",
        "feishu_doc": None,  # {token, url}
    }
    save_session(sess)
    return sess


def save_session(sess: dict):
    _session_path(sess["id"]).write_text(
        json.dumps(sess, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def load_session(sid: str):
    p = _session_path(sid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_sessions(limit: int = 60) -> list:
    out = []
    for p in sorted(SESSIONS_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
            out.append(
                {
                    "id": s.get("id", p.stem),
                    "title": s.get("title", ""),
                    "started": s.get("started", ""),
                    "turns": s.get("turns", 0),
                    "summary": s.get("summary", ""),
                    "doc_url": (s.get("feishu_doc") or {}).get("url", ""),
                }
            )
        except Exception:
            continue
    return out


def recent_summaries(n: int = 6) -> list:
    """最近 n 个已有小结的会话（供系统提示词提供跨会话记忆）"""
    out = []
    for meta in list_sessions(200):
        if meta["summary"]:
            out.append({"title": meta["title"] or meta["started"], "summary": meta["summary"], "started": meta["started"]})
        if len(out) >= n:
            break
    return out


def set_session_title_if_empty(sess: dict, first_text: str):
    if not sess.get("title"):
        t = first_text.strip().replace("\n", " ")[:16]
        sess["title"] = t or "树洞时刻"


# ---------------------------------------------------------------------------
# 人格模型
# ---------------------------------------------------------------------------
DEFAULT_PERSONA = {
    "version": 0,
    "updated_at": "",
    "summary": "刚认识的 TA —— 树洞还在慢慢了解中。",
    "traits": [],            # [{label, score, evidence}]
    "big5": {},              # {开放性, 尽责性, 外向性, 宜人性, 神经质}
    "emotional_baseline": "",
    "care_about": [],        # 在意的人和事
    "stressors": [],         # 压力源
    "energy_sources": [],    # 能量来源
    "communication_prefs": [],  # 偏好的沟通方式
    "memorable_quotes": [],  # 印象深刻的话
    "ai_notes": "",          # 给树洞自己的备忘：怎么陪 TA 聊
    "mood_log": [],          # [{ts, emotion, intensity, valence}]
    "history": [],           # 每次深度分析的历史快照 [{date, version, summary}]
}


def load_persona() -> dict:
    if PERSONA_FILE.exists():
        try:
            p = json.loads(PERSONA_FILE.read_text(encoding="utf-8"))
        except Exception:
            p = {}
    else:
        p = {}
    merged = dict(DEFAULT_PERSONA)
    merged.update({k: v for k, v in p.items() if k in DEFAULT_PERSONA})
    return merged


def save_persona(p: dict):
    p["updated_at"] = now_iso()
    PERSONA_FILE.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")


def log_mood(scan: dict):
    """每次快扫的情绪结果记入 mood_log（保留最近 500 条）"""
    p = load_persona()
    p["mood_log"].append(
        {
            "ts": now_iso(),
            "emotion": scan.get("emotion", "平静"),
            "intensity": int(scan.get("intensity", 30)),
            "valence": int(scan.get("valence", 0)),
        }
        )
    p["mood_log"] = p["mood_log"][-500:]
    save_persona(p)


# ---------------------------------------------------------------------------
# 运行状态（飞书文档映射等）
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"docs": {}, "persona_doc": None, "folder_token": "", "domain": ""}


def save_state(st: dict):
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def load_pending_feishu() -> list:
    if PENDING_FEISHU.exists():
        try:
            return json.loads(PENDING_FEISHU.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def append_pending_feishu(item: dict):
    items = load_pending_feishu()
    items.append(item)
    PENDING_FEISHU.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def clear_pending_feishu():
    if PENDING_FEISHU.exists():
        PENDING_FEISHU.unlink()
