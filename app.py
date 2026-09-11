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
from fastapi.responses import FileResponse, StreamingResponse
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
        "backup_api_key": "",
        "backup_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "backup_model": "",
    },
    "feishu": {
        "app_id": "",
        "app_secret": "",
        "folder_token": "",
        "auto_share_tenant": False,
    },
    "friend_name": "树洞",
}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if CONFIG_FILE.exists():
        try:
            user = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for section in ("llm", "feishu"):
                if isinstance(user.get(section), dict):
                    cfg[section].update({k: v for k, v in user[section].items()})
            if user.get("friend_name"):
                cfg["friend_name"] = user["friend_name"]
        except Exception as e:
            log.warning("config.json 解析失败，用默认配置: %s", e)
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
    yield
    await writer.flush()


app = FastAPI(title="AI 树洞挚友", lifespan=lifespan)


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
    for section in ("llm", "feishu"):
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
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
