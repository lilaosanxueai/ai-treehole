"""飞书云文档客户端

职责：
  - tenant_access_token 自动刷新
  - 根目录（或用户配置的文件夹）下建当日对话文档 / 人格画像文档
  - 把对话块追加写入文档（异步队列，失败落盘重试，不打断聊天）
  - MOCK 模式：TREEHOLE_MOCK=1 时不真正调飞书，方便离线联调
"""
import asyncio
import json
import logging
import os
import re
import time

import httpx

import store

log = logging.getLogger("treehole.feishu")

BASE = "https://open.feishu.cn/open-apis"
MOCK = os.environ.get("TREEHOLE_MOCK") == "1"


# ---------------------------------------------------------------------------
# 块构造（飞书 Docx Block 协议）
# ---------------------------------------------------------------------------
def _style(bold=False, italic=False):
    s = {}
    if bold:
        s["bold"] = True
    if italic:
        s["italic"] = True
    return s


def run(content, bold=False, italic=False):
    return {"content": content, "text_element_style": _style(bold, italic)}


def text_block(runs):
    return {
        "block_type": 2,
        "text": {"elements": [{"text_run": r} for r in runs], "style": {}},
    }


def heading_block(text, level=2):
    # heading1..9 → block_type 3..11
    level = max(1, min(4, level))
    return {
        "block_type": 2 + level,
        f"heading{level}": {"elements": [{"text_run": run(text)}], "style": {}},
    }


def bullet_block(text):
    return {
        "block_type": 12,
        "bullet": {"elements": [{"text_run": run(text)}], "style": {}},
    }


def divider_block():
    return {"block_type": 22, "divider": {}}


def turn_blocks(turn_no, time_str, user_text, ai_text, meta_line):
    """一轮对话 → 文档块"""
    blocks = [heading_block(f"第 {turn_no} 轮 · {time_str}", 3)]
    # 长文本按段拆分，避免单块超限
    blocks.append(text_block([run("🙋 我：", bold=True)]))
    for para in _split_paras(user_text):
        blocks.append(text_block([run(para)]))
    blocks.append(text_block([run("🌳 树洞：", bold=True)]))
    for para in _split_paras(ai_text):
        blocks.append(text_block([run(para)]))
    blocks.append(text_block([run(meta_line, italic=True)]))
    blocks.append(divider_block())
    return blocks


def session_head_blocks(time_str, title=""):
    t = f"（{title}）" if title else ""
    return [
        heading_block(f"—— 新对话 · {time_str} {t}", 2),
    ]


def summary_blocks(time_str, summary):
    blocks = [heading_block(f"📌 小结 · {time_str}", 2)]
    for line in summary.strip().splitlines():
        line = line.strip().lstrip("-•· ").strip()
        if line:
            blocks.append(bullet_block(line))
    blocks.append(divider_block())
    return blocks


def persona_intro_blocks():
    return [
        text_block([run("这份画像由「树洞挚友」根据对话记录自动分析与维护，随了解加深持续更新。", italic=True)]),
        text_block([run("它是树洞理解你的方式，也是只属于你的一本「自我说明书」。", italic=True)]),
        divider_block(),
    ]


def persona_snapshot_blocks(version, date_str, digest_lines):
    blocks = [heading_block(f"📋 画像 v{version} · {date_str}", 2)]
    for line in digest_lines:
        if line.startswith("# "):
            blocks.append(heading_block(line[2:].strip(), 3))
        else:
            blocks.append(bullet_block(line))
    blocks.append(divider_block())
    return blocks


def _split_paras(text, limit=1800):
    text = (text or "").strip()
    if not text:
        return ["（沉默）"]
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    out = []
    for p in paras:
        while len(p) > limit:
            out.append(p[:limit])
            p = p[limit:]
        out.append(p)
    return out or ["（沉默）"]


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
class FeishuError(RuntimeError):
    pass


class Feishu:
    def __init__(self, cfg: dict):
        self.app_id = (cfg.get("app_id") or "").strip()
        self.app_secret = (cfg.get("app_secret") or "").strip()
        self.folder_token_cfg = (cfg.get("folder_token") or "").strip()
        self.auto_share = bool(cfg.get("auto_share_tenant"))
        self._token = None
        self._token_exp = 0.0
        self.http = httpx.Client(timeout=30)
        self._domain = ""

    # ---- 基础 -------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.app_id and self.app_secret)

    def _get_token(self):
        if MOCK:
            return "mock-token"
        if self._token and time.time() < self._token_exp - 300:
            return self._token
        r = self.http.post(
            f"{BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        data = r.json()
        if data.get("code") != 0:
            raise FeishuError(f"获取 token 失败: {data.get('msg')} (app_id={self.app_id[:10]}...)")
        self._token = data["tenant_access_token"]
        self._token_exp = time.time() + data.get("expire", 7200)
        return self._token

    def _api(self, method, path, body=None, params=None):
        if MOCK:
            return {"mock": True}
        r = self.http.request(
            method,
            f"{BASE}{path}",
            json=body,
            params=params,
            headers={"Authorization": "Bearer " + self._get_token()},
        )
        data = r.json()
        if data.get("code") != 0:
            raise FeishuError(f"[{method} {path}] code={data.get('code')} {data.get('msg')}")
        return data.get("data") or {}

    # ---- 文件夹 / 文档 ----------------------------------------------------
    def domain(self) -> str:
        """飞书租户域名（拼文档链接用），缓存进 state"""
        if MOCK:
            return "example.com"
        if self._domain:
            return self._domain
        st = store.load_state()
        if st.get("domain"):
            self._domain = st["domain"]
            return self._domain
        # 兜底：列根目录文件，从任一文件链接里取域名
        try:
            root = self._api("GET", "/drive/explorer/v2/root_folder/meta").get("token", "root")
            files = self._api(
                "GET", "/drive/v1/files",
                params={"folder_token": root, "page_size": 5, "user_id_type": "open_id"},
            ).get("files") or []
            for f in files:
                if f.get("url"):
                    self._domain = f["url"].split("/")[2]
                    st["domain"] = self._domain
                    store.save_state(st)
                    return self._domain
        except Exception:
            pass
        self._domain = "feishu.cn"
        return self._domain

    def doc_url(self, doc_id: str) -> str:
        return f"https://{self.domain()}/docx/{doc_id}"

    def _parse_folder_input(self):
        """folder_token 配置支持直接填 token（fld…）或完整文件夹链接"""
        v = self.folder_token_cfg
        if not v:
            return "", ""
        m = re.search(r"folder/([A-Za-z0-9]+)", v)
        if m:
            domain = v.split("/")[2] if v.startswith("http") else ""
            return m.group(1), domain
        return v.strip(), ""

    def ensure_folder(self) -> str:
        """返回工作文件夹 token：优先用户配置；否则在应用根目录建「🌳 AI树洞」并缓存"""
        if MOCK:
            return "mock-folder"
        st = store.load_state()
        token, domain = self._parse_folder_input()
        if domain and not st.get("domain"):
            self._domain = domain
            st["domain"] = domain
            store.save_state(st)
        if token:
            return token
        if st.get("folder_token"):
            return st["folder_token"]
        root = self._api("GET", "/drive/explorer/v2/root_folder/meta").get("token", "")
        data = self._api(
            "POST", f"/drive/explorer/v2/folder/{root}",
            body={"title": "🌳 AI树洞"},
        )
        token = data.get("token", "")
        if not token:
            raise FeishuError(f"创建根文件夹失败: {data}")
        if data.get("url"):  # 建文件夹的返回里带完整链接，顺手记下域名
            self._domain = data["url"].split("/")[2]
            st["domain"] = self._domain
        st["folder_token"] = token
        store.save_state(st)
        return token

    def _share_doc(self, doc_id: str):
        """把文档设为组织内可编辑（个人租户里实际只有你自己能看，链接可直接打开）
        注意：该接口不支持 folder 类型，只能对文档本身设置"""
        try:
            self._api(
                "PATCH", f"/drive/v1/permissions/{doc_id}/public",
                body={"link_share_entity": "tenant_editable"},
                params={"type": "docx"},
            )
        except Exception as e:
            log.warning("设置文档共享失败（不影响写入，可在飞书里手动开）: %s", e)

    def create_doc(self, title: str, require_folder: bool = False) -> dict:
        """require_folder=True：文件夹不可用就直接失败（每日记录/画像文档必须建在你看得见的地方）"""
        if MOCK:
            return {"token": f"mockdoc{int(time.time())}", "url": "https://example.com/docx/mock"}
        body = {"title": title}
        folder_problem = None
        try:
            body["folder_token"] = self.ensure_folder()
        except Exception as e:
            folder_problem = e
            if require_folder:
                raise
        data = self._api("POST", "/docx/v1/documents", body=body)
        doc_id = (data.get("document") or {}).get("document_id", "")
        if not doc_id:
            raise FeishuError(f"创建文档失败: {data}")
        if folder_problem:
            log.warning("文件夹不可用（%s），文档建在应用空间：%s", folder_problem, title)
        if self.auto_share:
            self._share_doc(doc_id)
        return {"token": doc_id, "url": self.doc_url(doc_id)}

    def ensure_daily_doc(self) -> dict:
        """当日对话文档（一天一篇，存在 state 里，重启不重复建）；必须有文件夹才建"""
        st = store.load_state()
        key = store.today()
        doc = st.get("docs", {}).get(key)
        if doc and doc.get("token"):
            return doc
        doc = self.create_doc(f"🌳 树洞 · {store.today_label()}", require_folder=True)
        self.append_blocks(doc["token"], [
            text_block([run("今天的心事，都讲给树洞听。树洞会认真记下每一句话 🌳", italic=True)]),
            divider_block(),
        ])
        st = store.load_state()  # create_doc/ensure_folder 可能已更新过 state，重新读避免覆盖
        st.setdefault("docs", {})[key] = doc
        store.save_state(st)
        return doc

    def ensure_persona_doc(self) -> dict:
        st = store.load_state()
        doc = st.get("persona_doc")
        if doc and doc.get("token"):
            return doc
        doc = self.create_doc("💎 树洞人格画像", require_folder=True)
        self.append_blocks(doc["token"], persona_intro_blocks())
        st = store.load_state()  # 同上，防止旧快照覆盖
        st["persona_doc"] = doc
        store.save_state(st)
        return doc

    def send_im(self, chat_id: str, text: str):
        """给飞书会话（群/机器人对话）发文本消息：主动关怀、周报推送用"""
        if MOCK:
            log.info("[MOCK 飞书] IM → %s: %s", chat_id, text[:40])
            return True
        try:
            self._api(
                "POST", "/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                body={
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
            )
            return True
        except Exception as e:
            log.warning("飞书 IM 推送失败: %s", e)
            return False

    def weekly_report_blocks(self, range_label, stats, persona_summary, highlights):
        """情绪周报 → 文档块"""
        blocks = [heading_block(f"🌳 树洞周报 · {range_label}", 2)]
        blocks.append(text_block([run(f"这七天里，你们聊了 {stats['turns']} 轮、{stats['sessions']} 次坐进树洞。", bold=True)]))
        blocks.append(heading_block("🌡️ 情绪曲线", 3))
        blocks.append(bullet_block(f"出现最多的情绪：{stats['top_emotions']}"))
        blocks.append(bullet_block(f"整体基调：{stats['tone']}（均值 {stats['avg_valence']}，前半段 {stats['val_first']} → 后半段 {stats['val_last']}）"))
        blocks.append(bullet_block(f"平均情绪强度：{stats['avg_intensity']}/100"))
        blocks.append(heading_block("🌱 画像此刻", 3))
        blocks.append(text_block([run(persona_summary or "（画像还在慢慢成形）")]))
        if highlights:
            blocks.append(heading_block("📌 这一周的小事", 3))
            for h in highlights:
                blocks.append(bullet_block(h))
        blocks.append(text_block([run("—— 树洞会一直在这儿，随时回来坐坐。", italic=True)]))
        blocks.append(divider_block())
        return blocks

    # ---- 块写入 ------------------------------------------------------------
    def append_blocks(self, doc_id: str, blocks: list):
        """追加块到文档末尾（页块 id 即 document_id），每次最多 20 块"""
        if MOCK:
            log.info("[MOCK 飞书] 向文档 %s 追加 %d 块", doc_id, len(blocks))
            return
        for i in range(0, len(blocks), 20):
            chunk = blocks[i : i + 20]
            self._api(
                "POST",
                f"/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
                body={"children": chunk, "index": -1},
            )

    def check(self) -> dict:
        """连通自检：token / 文档读写 / 文件夹，分项报告"""
        result = {"ok": False, "app_id": self.app_id, "msg": ""}
        if MOCK:
            return {"ok": True, "msg": "MOCK 模式"}
        try:
            if not self.enabled:
                result["msg"] = "未配置 app_id / app_secret"
                return result
            self._get_token()
        except Exception as e:
            result["msg"] = str(e)
            return result
        # 文档读写（docx:document）
        docx_ok, docx_msg = True, ""
        try:
            doc = self.create_doc("🌳 树洞 · 自检（可删除）")
            self.append_blocks(doc["token"], [text_block([run("自检")]), divider_block()])
            try:
                self._api("DELETE", f"/drive/v1/files/{doc['token']}", params={"type": "docx"})
            except Exception:
                docx_msg = "（自检文档已建在应用空间，可手动删）"
        except Exception as e:
            docx_ok, docx_msg = False, str(e)[:120]
        # 文件夹（drive:drive）
        folder_ok, folder_msg = True, ""
        try:
            result["folder_token"] = self.ensure_folder()
        except Exception as e:
            folder_ok, folder_msg = False, str(e)[:160]
        result["ok"] = docx_ok
        if docx_ok and folder_ok:
            result["msg"] = "飞书连接正常（文档 + 文件夹）"
        elif docx_ok:
            result["msg"] = ("文档读写正常，但文件夹权限缺失：记录会建在应用空间，你打不开。"
                             "请在开放平台开通 drive:drive 并发布版本" + (folder_msg and f"（{folder_msg}）" or ""))
        else:
            result["msg"] = docx_msg
        return result


# ---------------------------------------------------------------------------
# 异步写入队列：聊天主流程只入队，飞书慢/挂都不影响对话
# ---------------------------------------------------------------------------
class FeishuWriter:
    def __init__(self, fs: Feishu):
        self.fs = fs
        self.queue: asyncio.Queue = asyncio.Queue()
        self._task = None

    def start(self):
        if self._task is None or self._task.done():
            for item in store.load_pending_feishu():
                self.queue.put_nowait(item)
            store.clear_pending_feishu()
            self._task = asyncio.get_event_loop().create_task(self._worker())
            log.info("飞书写入队列已启动（待写 %d 组块）", self.queue.qsize())

    async def enqueue(self, doc_id: str, blocks: list, label: str = ""):
        if not self.fs.enabled and not MOCK:
            return
        await self.queue.put({"doc": doc_id, "blocks": blocks, "label": label})

    async def _worker(self):
        while True:
            item = await self.queue.get()
            for attempt in range(3):
                try:
                    await asyncio.to_thread(self.fs.append_blocks, item["doc"], item["blocks"])
                    break
                except Exception as e:
                    log.warning("飞书写入失败(%s) 第%d次: %s", item.get("label"), attempt + 1, e)
                    if attempt == 2:
                        store.append_pending_feishu(item)
                        log.error("飞书写入最终失败，已落盘待恢复: %s", item.get("label"))
                    else:
                        await asyncio.sleep(2 * (attempt + 1))
            self.queue.task_done()

    async def flush(self):
        """等队列清空（用于测试/退出前）"""
        await self.queue.join()
