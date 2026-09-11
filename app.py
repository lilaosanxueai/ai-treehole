"""AI 树洞挚友 —— 后端服务

一句话：把心事讲给树洞听；对话实时存进飞书云文档；大脑持续分析你的
性格、情绪与人格画像，并据此扮演倾听者 / 交流者 / 分享者 / 安抚者。

启动：python app.py        （默认 http://127.0.0.1:8311）
自检：python app.py --check
联调：TREEHOLE_MOCK=1 python app.py --check   （不真正调飞书/大模型）
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台中文
    sys.stderr.reconfigure(encoding="utf-8")

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import brain
import feishu
import store
from brain import LLM, ROLES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("treehole")

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
PORT = 8311

DEFAULT_CONFIG = {
    "llm": {
        "api_key": "",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.6",
        "fast_model": "glm-4-flash",
        "thinking_mode": "smart",  # smart=轻任务关思考(快) 重任务开 | always | never
        "backup_api_key": "",
        "backup_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "backup_model": "",
    },
    "feishu": {
        "app_id": "",
        "app_secret": "",
        "folder_token": "",
        "auto_share_tenant": False,
        "notify_chat_id": "",  # 主动关怀/周报推送的飞书会话（可留空）
    },
    "server": {
        "lan": False,          # 手机等局域网设备访问
        "access_token": "",    # 局域网访问令牌（开启 lan 时自动生成）
    },
    "friend_name": "树洞",
}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if CONFIG_FILE.exists():
        try:
            user = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for section in ("llm", "feishu", "server"):
                if isinstance(user.get(section), dict):
                    cfg[section].update({k: v for k, v in user[section].items()})
            if user.get("friend_name"):
                cfg["friend_name"] = user["friend_name"]
        except Exception as e:
            log.warning("config.json 解析失败，用默认配置: %s", e)
    # 局域网开启时保证有访问令牌
    if cfg["server"].get("lan") and not cfg["server"].get("access_token"):
        import secrets
        cfg["server"]["access_token"] = secrets.token_hex(4)
        try:
            Path(CONFIG_FILE).write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return cfg


def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


CFG = load_config()
llm = LLM(CFG["llm"])
fs = feishu.Feishu(CFG["feishu"])
writer = feishu.FeishuWriter(fs)

# 深度画像自动更新：自上次分析以来累计轮数达到阈值就后台跑一次
DEEP_ANALYZE_TURNS = 40


@asynccontextmanager
async def lifespan(_app: FastAPI):
    writer.start()
    log.info("树洞已苏醒 · 模型=%s 飞书=%s", llm.model if llm.ready else "未配置",
             "已配置" if (fs.enabled or feishu.MOCK) else "未配置")
    care_task = asyncio.get_event_loop().create_task(_care_loop())
    yield
    care_task.cancel()
    await writer.flush()


app = FastAPI(title="AI 树洞挚友", lifespan=lifespan)


# ---------------------------------------------------------------------------
# 局域网访问令牌：lan 开启时 /api/* 需带 X-Treehole-Token 头或 ?t= 查询参数
# ---------------------------------------------------------------------------
OPEN_PATHS = {"/api/lan", "/api/lan_bad"}


@app.middleware("http")
async def token_guard(request, call_next):
    if CFG["server"].get("lan"):
        p = request.url.path
        if p.startswith("/api") and p not in OPEN_PATHS:
            supplied = request.headers.get("x-treehole-token") or request.query_params.get("t", "")
            if supplied != CFG["server"].get("access_token"):
                return JSONResponse({"detail": "缺少或错误的访问令牌"}, status_code=401)
    return await call_next(request)


def _lan_ip() -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@app.get("/api/lan")
async def api_lan():
    """局域网信息：手机扫码/输地址用（此接口本身不带令牌）"""
    lan_on = bool(CFG["server"].get("lan"))
    out = {"lan": lan_on, "need_token": lan_on}
    if lan_on:
        out["url"] = f"http://{_lan_ip()}:{PORT}/"
        out["token"] = CFG["server"].get("access_token", "")
    return out


@app.get("/api/qr")
async def api_qr():
    """局域网地址的二维码（SVG），仅 lan 开启时可用"""
    if not CFG["server"].get("lan"):
        raise HTTPException(400, "局域网访问未开启")
    import segno
    url = f"http://{_lan_ip()}:{PORT}/?t={CFG['server'].get('access_token', '')}"
    svg = segno.make(url, error="m").svg_data_uri(scale=6)
    return {"url": url, "svg": svg}


# ---------------------------------------------------------------------------
# 静态页面
# ---------------------------------------------------------------------------
@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html",
                        headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


# ---------------------------------------------------------------------------
# 状态 / 会话
# ---------------------------------------------------------------------------
def _mood_recent(n=60):
    return store.load_persona()["mood_log"][-n:]


@app.get("/api/state")
async def api_state():
    persona = store.load_persona()
    st = store.load_state()
    today_doc = st.get("docs", {}).get(store.today()) or {}
    return {
        "ok": True,
        "llm_ready": llm.ready,
        "model": llm.model,
        "feishu_ready": fs.enabled or feishu.MOCK,
        "folder_token": CFG["feishu"].get("folder_token", ""),
        "today_doc_url": today_doc.get("url", ""),
        "persona_doc_url": (st.get("persona_doc") or {}).get("url", ""),
        "friend_name": CFG.get("friend_name", "树洞"),
        "persona": persona,
        "sessions": store.list_sessions(40),
        "roles": ROLES,
        "hotlines": [
            {"name": "全国心理援助热线", "tel": "12356"},
            {"name": "北京心理危机干预中心", "tel": "010-82951332"},
            {"name": "希望24热线", "tel": "400-161-9995"},
        ],
    }


class SessionIn(BaseModel):
    session_id: str = ""


@app.post("/api/session/new")
async def api_new_session():
    sess = store.new_session()
    return {"ok": True, "session": sess}


@app.post("/api/session/load")
async def api_load_session(body: SessionIn):
    sess = store.load_session(body.session_id)
    if not sess:
        raise HTTPException(404, "会话不存在")
    return {"ok": True, "session": sess}


# ---------------------------------------------------------------------------
# 聊天（SSE 流式）
# ---------------------------------------------------------------------------
class ChatIn(BaseModel):
    session_id: str
    text: str
    role_mode: str = "auto"  # auto / listener / talker / sharer / soother


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _ensure_today_doc() -> dict:
    """拿当日飞书文档；飞书不可用时返回空 dict（本地照常聊）"""
    try:
        return await asyncio.to_thread(fs.ensure_daily_doc)
    except Exception as e:
        log.warning("获取当日飞书文档失败: %s", e)
        return {}


@app.post("/api/chat")
async def api_chat(body: ChatIn):
    sess = store.load_session(body.session_id)
    if not sess:
        raise HTTPException(404, "会话不存在，请先新建")
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "说点什么吧")
    store.set_session_title_if_empty(sess, text)
    summaries = store.recent_summaries(6)
    recent_tail = "\n".join(
        f"{'我' if m['role'] == 'user' else '树洞'}：{m['content']}" for m in sess["messages"][-6:]
    )

    async def gen():
        # 1) 情绪快扫（决定角色 + 记录情绪）
        scan = await brain.quick_scan(llm, text, recent_tail)
        manual = body.role_mode if body.role_mode in brain.VALID_ROLES else None
        role = manual or scan["role"]
        if scan.get("risk") == "high":
            role = "soother"
        yield _sse("scan", {"scan": scan, "role": role, "role_label": ROLES.get(role, {}).get("label", role)})

        # 2) 组装提示词，流式回复
        system = brain.build_system_prompt(
            store.load_persona(), summaries, role, scan, CFG.get("friend_name", "树洞")
        )
        sess["messages"].append({"role": "user", "content": text,
                                 "ts": store.now_iso(), "scan": scan, "role": role})
        msgs = brain.build_messages(sess, system)
        buf = []
        try:
            async for delta in llm.chat_stream(msgs):
                buf.append(delta)
                yield _sse("delta", {"text": delta})
        except Exception as e:
            log.error("对话流式失败: %s", e)
            yield _sse("error", {"msg": f"树洞走神了（模型调用失败）：{e}"})
            sess["messages"].pop()  # 本轮作废
            store.save_session(sess)
            return

        reply = "".join(buf).strip()
        sess["messages"].append({"role": "assistant", "content": reply, "ts": store.now_iso()})
        sess["turns"] = sess.get("turns", 0) + 1
        store.save_session(sess)
        store.log_mood(scan)

        # 3) 异步写飞书（不阻塞聊天）；此前因飞书不可用而漏写的轮次一并补上
        doc = await _ensure_today_doc()
        feishu_saved = False
        if doc.get("token"):
            blocks = []
            start_turn = sess.get("synced_turns", 0) + 1  # 未记录过同步的会话从第 1 轮全部补写
            for t in range(start_turn, sess["turns"] + 1):
                i = (t - 1) * 2
                if i + 1 >= len(sess["messages"]):
                    break  # 数据不完整时只补能配对的轮次
                um, am = sess["messages"][i], sess["messages"][i + 1]
                if t == 1:
                    blocks += feishu.session_head_blocks((um.get("ts") or store.now_iso())[11:16], sess["title"])
                st_ = um.get("scan") or scan
                role_name = ROLES.get(um.get("role") or st_.get("role", ""), {}).get("name",
                            um.get("role") or st_.get("role", ""))
                meta = (f"　· 情绪 {st_.get('emotion', '—')} {st_.get('intensity', '')} · "
                        f"话题 {'、'.join(st_.get('topics') or []) or '—'} · 角色 {role_name}")
                blocks += feishu.turn_blocks(t, (um.get("ts") or store.now_iso())[11:16],
                                             um["content"], am["content"], meta)
            if blocks:
                await writer.enqueue(doc["token"], blocks, label=f"turn{sess['turns']}")
            sess["synced_turns"] = sess["turns"]
            if not sess.get("feishu_doc"):
                sess["feishu_doc"] = doc
            store.save_session(sess)
            feishu_saved = True

        # 4) 后台收尾：每 8 轮小结 + 累计 40 轮自动深度画像
        asyncio.create_task(_finalize(sess["id"], doc))
        yield _sse("done", {
            "turns": sess["turns"],
            "doc_url": doc.get("url", ""),
            "feishu_saved": feishu_saved,
            "risk": scan.get("risk", "none"),
        })

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _finalize(session_id: str, doc: dict):
    """流结束后台任务：生成小结、写飞书、必要时深度分析画像"""
    try:
        sess = store.load_session(session_id)
        if not sess or not sess["messages"]:
            return
        if sess["turns"] % 8 == 0:
            summary = await brain.summarize_session(llm, sess)
            if summary:
                sess["summary"] = summary
                store.save_session(sess)
                if doc.get("token"):
                    await writer.enqueue(
                        doc["token"],
                        feishu.summary_blocks(store.now_iso()[11:16], summary),
                        label="summary",
                    )
        # 自动深度画像：距上次分析累计轮数够多
        persona = store.load_persona()
        done_turns = persona.get("analyzed_turns", 0)
        total_turns = sum(s.get("turns", 0) for s in store.list_sessions(200))
        if total_turns - done_turns >= DEEP_ANALYZE_TURNS:
            await refresh_persona_internal()
    except Exception as e:
        log.warning("收尾任务失败: %s", e)


# ---------------------------------------------------------------------------
# 主动关怀 + 情绪周报
# ---------------------------------------------------------------------------
from datetime import datetime, timedelta


def _parse_ts(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _weekly_stats():
    """近 7 天情绪与会话统计"""
    persona = store.load_persona()
    cutoff = datetime.now() - timedelta(days=7)
    logs = [m for m in persona.get("mood_log", [])
            if (t := _parse_ts(m.get("ts", ""))) and t >= cutoff]
    sessions = [s for s in store.list_sessions(200)
                if (t := _parse_ts(s.get("started", ""))) and t >= cutoff]
    turns = sum(s.get("turns", 0) for s in sessions)
    if logs:
        cnt = {}
        for m in logs:
            cnt[m["emotion"]] = cnt.get(m["emotion"], 0) + 1
        top = "、".join(f"{k}×{v}" for k, v in sorted(cnt.items(), key=lambda x: -x[1])[:3])
        avg_v = round(sum(m.get("valence", 0) for m in logs) / len(logs), 2)
        avg_i = round(sum(m.get("intensity", 0) for m in logs) / len(logs))
        half = len(logs) // 2 or 1
        v1 = round(sum(m.get("valence", 0) for m in logs[:half]) / half, 2)
        v2 = round(sum(m.get("valence", 0) for m in logs[half:]) / max(len(logs) - half, 1), 2)
        tone = "偏沉一些" if avg_v < -0.6 else "整体平稳" if avg_v < 0.4 else "亮着光"
    else:
        top, avg_v, avg_i, v1, v2, tone = "—", 0, 0, 0, 0, "还没有记录"
    highlights = [f"{s['started'][5:16]}（{s['title'] or '树洞时刻'}）：{s['summary'].splitlines()[0]}"
                  for s in sessions if s.get("summary")][:5]
    return {
        "sessions": len(sessions), "turns": turns,
        "top_emotions": top, "avg_valence": avg_v, "avg_intensity": avg_i,
        "val_first": v1, "val_last": v2, "tone": tone,
        "highlights": highlights,
    }


async def generate_weekly_report(push: bool = True) -> dict:
    """生成《树洞周报》文档（+可选 IM 推送摘要）"""
    stats = _weekly_stats()
    persona = store.load_persona()
    now = datetime.now()
    label = f"{(now - timedelta(days=6)).strftime('%m.%d')}–{now.strftime('%m.%d')}"
    doc = await asyncio.to_thread(fs.create_doc, f"🌳 树洞周报 · {label}", True)
    blocks = fs.weekly_report_blocks(label, stats, persona.get("summary", ""), stats["highlights"])
    await writer.enqueue(doc["token"], blocks, label="weekly")
    st = store.load_state()
    st["last_report_week"] = f"{now.isocalendar().year}-{now.isocalendar().week}"
    store.save_state(st)
    pushed = False
    chat_id = CFG["feishu"].get("notify_chat_id", "")
    if push and chat_id:
        pushed = await asyncio.to_thread(
            fs.send_im, chat_id,
            f"🌳 树洞周报 · {label}\n这七天聊了 {stats['turns']} 轮 · 情绪基调：{stats['tone']}\n{doc['url']}"
        )
    return {"ok": True, "url": doc["url"], "stats": stats, "pushed": pushed}


@app.post("/api/report/weekly")
async def api_weekly_report():
    try:
        return await generate_weekly_report()
    except Exception as e:
        return {"ok": False, "msg": str(e)[:200]}


CARE_HELLOS = [
    "🌳 好几天没听见你的声音了，树洞一直给你留着位置。想来坐坐随时来。",
    "🌳 最近还好吗？不忙的时候，树洞想听你说说话。",
    "🌳 无论这阵子过得顺不顺，树洞都在老地方。",
]
CARE_LOWMOOD = [
    "🌳 这几次聊天里，你好像都挺沉的。不用硬撑，想说话的时候我都在。如果很难受，心理援助热线 12356 随时可以打。",
]


async def _care_loop():
    """每 30 分钟巡检：久未来访 / 持续低落 → 飞书轻问候；周日 20 点出周报"""
    await asyncio.sleep(60)
    while True:
        try:
            chat_id = CFG["feishu"].get("notify_chat_id", "")
            st = store.load_state()
            now = datetime.now()
            if chat_id and fs.enabled:
                # 久未来访（>3 天有历史会话才提醒）
                sessions = store.list_sessions(200)
                if sessions:
                    last = _parse_ts(sessions[0]["started"])
                    lc = _parse_ts(st.get("last_care_ts", "") or "2000-01-01 00:00:00")
                    if last and (now - last) > timedelta(days=3) and (now - lc) > timedelta(days=3):
                        await asyncio.to_thread(fs.send_im, chat_id,
                                                CARE_HELLOS[now.hour % len(CARE_HELLOS)])
                        st["last_care_ts"] = store.now_iso()
                        store.save_state(st)
                    # 持续低落（近 14 条均值 valence ≤ -1.2，跨度 >2 天）
                    persona = store.load_persona()
                    logs = persona.get("mood_log", [])[-14:]
                    lm = _parse_ts(st.get("last_lowcare_ts", "") or "2000-01-01 00:00:00")
                    if len(logs) >= 6:
                        a, b = _parse_ts(logs[0]["ts"]), _parse_ts(logs[-1]["ts"])
                        avg = sum(m.get("valence", 0) for m in logs) / len(logs)
                        if a and b and (b - a) > timedelta(days=2) and avg <= -1.2 and (now - lm) > timedelta(days=7):
                            await asyncio.to_thread(fs.send_im, chat_id, CARE_LOWMOOD[0])
                            st["last_lowcare_ts"] = store.now_iso()
                            store.save_state(st)
            # 周日 20 点自动周报
            week_key = f"{now.isocalendar().year}-{now.isocalendar().week}"
            if now.weekday() == 6 and now.hour >= 20 and st.get("last_report_week") != week_key:
                await generate_weekly_report()
        except Exception as e:
            log.warning("关怀巡检失败: %s", e)
        await asyncio.sleep(1800)


# ---------------------------------------------------------------------------
# 人格画像
# ---------------------------------------------------------------------------
async def refresh_persona_internal() -> dict:
    persona = store.load_persona()
    summaries = store.recent_summaries(10)
    recent = []
    for meta in store.list_sessions(10):
        s = store.load_session(meta["id"])
        if s:
            recent.extend(s["messages"][-12:])
    new_fields = await brain.deep_analyze(llm, persona, summaries, recent)
    if not new_fields:
        return {"ok": False, "msg": "分析失败（模型无有效输出）", "persona": persona}
    persona.update({k: v for k, v in new_fields.items() if k in store.DEFAULT_PERSONA})
    persona["version"] = persona.get("version", 0) + 1
    persona["analyzed_turns"] = sum(s.get("turns", 0) for s in store.list_sessions(200))
    persona.setdefault("history", []).append(
        {"date": store.now_iso(), "version": persona["version"], "summary": new_fields.get("summary", "")[:80]}
    )
    persona["history"] = persona["history"][-30:]
    store.save_persona(persona)
    # 同步到飞书人格文档
    try:
        pdoc = await asyncio.to_thread(fs.ensure_persona_doc)
        if pdoc.get("token"):
            lines = brain.persona_digest_lines(new_fields)
            await writer.enqueue(
                pdoc["token"],
                feishu.persona_snapshot_blocks(persona["version"], store.now_iso()[:16], lines),
                label="persona",
            )
    except Exception as e:
        log.warning("画像写飞书失败: %s", e)
    return {"ok": True, "msg": f"画像已更新到 v{persona['version']}", "persona": persona}


@app.post("/api/persona/refresh")
async def api_persona_refresh():
    result = await refresh_persona_internal()
    return result


# ---------------------------------------------------------------------------
# 设置 / 自检
# ---------------------------------------------------------------------------
class ConfigIn(BaseModel):
    llm: dict = {}
    feishu: dict = {}
    server: dict = {}
    friend_name: str = ""


@app.get("/api/config")
async def api_get_config():
    """当前配置回填（密钥脱敏显示，前端保存时跳过未改动的脱敏值）"""
    def mask(s: str) -> str:
        s = s or ""
        return s if len(s) <= 8 else s[:5] + "****" + s[-4:]
    return {
        "llm": {
            "api_key": mask(CFG["llm"].get("api_key")),
            "has_key": bool(CFG["llm"].get("api_key")),
            "base_url": CFG["llm"].get("base_url", ""),
            "model": CFG["llm"].get("model", ""),
            "fast_model": CFG["llm"].get("fast_model", ""),
            "thinking_mode": CFG["llm"].get("thinking_mode", "smart"),
            "backup_api_key": mask(CFG["llm"].get("backup_api_key")),
            "has_backup_key": bool(CFG["llm"].get("backup_api_key")),
            "backup_base_url": CFG["llm"].get("backup_base_url", ""),
            "backup_model": CFG["llm"].get("backup_model", ""),
        },
        "feishu": {
            "app_id": CFG["feishu"].get("app_id", ""),
            "app_secret": mask(CFG["feishu"].get("app_secret")),
            "has_secret": bool(CFG["feishu"].get("app_secret")),
            "folder_token": CFG["feishu"].get("folder_token", ""),
            "auto_share_tenant": bool(CFG["feishu"].get("auto_share_tenant")),
            "notify_chat_id": CFG["feishu"].get("notify_chat_id", ""),
        },
        "server": {
            "lan": bool(CFG["server"].get("lan")),
            "access_token": CFG["server"].get("access_token", ""),
        },
        "friend_name": CFG.get("friend_name", "树洞"),
    }


@app.post("/api/config")
async def api_config(body: ConfigIn):
    global CFG, llm, fs

    def mask(s: str) -> str:
        s = s or ""
        return s if len(s) <= 8 else s[:5] + "****" + s[-4:]

    # 脱敏值/含掩码的输入不覆盖真实密钥（前端回填的是脱敏值，直连 API 也拦住）
    SECRET_FIELDS = [("llm", "api_key"), ("llm", "backup_api_key"), ("feishu", "app_secret")]
    current = {(s, k): CFG[s].get(k, "") for s, k in SECRET_FIELDS}
    for section in ("llm", "feishu", "server"):
        incoming = getattr(body, section) or {}
        for k, v in incoming.items():
            if k not in DEFAULT_CONFIG[section]:
                continue
            if (section, k) in current:
                if v == mask(current[(section, k)]) or "****" in str(v):
                    continue  # 未改动的脱敏值，跳过
            CFG[section][k] = v
    if body.friend_name.strip():
        CFG["friend_name"] = body.friend_name.strip()
    if CFG["server"].get("lan") and not CFG["server"].get("access_token"):
        import secrets
        CFG["server"]["access_token"] = secrets.token_hex(4)
    save_config(CFG)
    llm = LLM(CFG["llm"])
    fs = feishu.Feishu(CFG["feishu"])
    globals()["llm"] = llm
    globals()["fs"] = fs
    globals()["writer"].fs = fs
    return {"ok": True, "msg": "已保存（敏感信息只存在本机 config.json）"}


@app.post("/api/feishu/check")
async def api_feishu_check():
    if not (fs.enabled or feishu.MOCK):
        return {"ok": False, "msg": "请先填写飞书 app_id / app_secret"}
    return await asyncio.to_thread(fs.check)


@app.post("/api/llm/check")
async def api_llm_check():
    return await llm.ping()


# ---------------------------------------------------------------------------
# 自检 / 启动
# ---------------------------------------------------------------------------
def run_check():
    print("=" * 52)
    print("AI 树洞挚友 · 环境自检" + ("（MOCK 模式）" if feishu.MOCK else ""))
    print("=" * 52)
    r = fs.check()
    print(f"[飞书] {'✅' if r['ok'] else '❌'} {r['msg']}")
    if r.get("folder_token"):
        print(f"       工作文件夹: {r['folder_token']}")

    async def _ping():
        return await llm.ping()
    lr = asyncio.run(_ping())
    print(f"[模型] {'✅' if lr['ok'] else '❌'} {lr['msg']}")
    print(f"[本地] ✅ data/ 目录: {store.DATA_DIR}")
    ok = r["ok"] and lr["ok"]
    print("=" * 52)
    print("✅ 全部就绪，运行 python app.py 开始" if ok else "❌ 有未就绪项，按上面提示修复（见 README）")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(run_check())
    if not feishu.MOCK and "--no-browser" not in sys.argv:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{PORT}")
    host = "0.0.0.0" if CFG["server"].get("lan") else "127.0.0.1"
    if CFG["server"].get("lan"):
        log.info("局域网访问已开启: http://%s:%s/ 令牌 %s（首次会弹防火墙授权）",
                 _lan_ip(), PORT, CFG["server"].get("access_token"))
    uvicorn.run(app, host=host, port=PORT, log_level="info")
