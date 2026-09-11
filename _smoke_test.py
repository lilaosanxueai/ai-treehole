"""端到端冒烟测试（MOCK 模式）：会话 → SSE 聊天 → 情绪快扫 → 画像更新 → 本地落盘"""
import json
import sys

import httpx

BASE = "http://127.0.0.1:8311"


def main():
    c = httpx.Client(timeout=60)

    # 1. 新会话
    r = c.post(f"{BASE}/api/session/new").json()
    sid = r["session"]["id"]
    print("✅ 新会话:", sid)

    # 2. SSE 聊天（负面情绪 + 求建议）
    body = {"session_id": sid, "role_mode": "auto",
            "text": "今天被领导当众批评了，项目延期的锅全扣我头上，真的很难受很委屈！"}
    events = []
    with c.stream("POST", f"{BASE}/api/chat", json=body) as resp:
        assert resp.status_code == 200, resp.text
        buf = ""
        for chunk in resp.iter_text():
            buf += chunk
            while "\n\n" in buf:
                raw, buf = buf.split("\n\n", 1)
                ev, data = None, {}
                for line in raw.split("\n"):
                    if line.startswith("event:"):
                        ev = line[6:].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                events.append((ev, data))
    kinds = [e for e, _ in events]
    print("✅ SSE 事件序列:", kinds)
    assert "scan" in kinds and "delta" in kinds and "done" in kinds
    scan = dict(events[kinds.index("scan")][1])["scan"]
    done = dict(events[kinds.index("done")][1])
    print("   情绪快扫:", json.dumps(scan, ensure_ascii=False))
    print("   done:", json.dumps(done, ensure_ascii=False))

    # 3. 高风险词 → 守护模式（soother + risk=high）
    body2 = {"session_id": sid, "role_mode": "auto", "text": "有时候真的觉得活不下去了"}
    events2 = []
    with c.stream("POST", f"{BASE}/api/chat", json=body2) as resp:
        buf = ""
        for chunk in resp.iter_text():
            buf += chunk
            while "\n\n" in buf:
                raw, buf = buf.split("\n\n", 1)
                ev, data = None, {}
                for line in raw.split("\n"):
                    if line.startswith("event:"):
                        ev = line[6:].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                events2.append((ev, data))
    scan2 = dict(events2[[e for e, _ in events2].index("scan")][1])["scan"]
    print("✅ 高风险检测:", scan2["risk"], "→ 角色", dict(events2[[e for e, _ in events2].index("scan")][1])["role"])
    assert scan2["risk"] == "high"

    # 4. 画像更新
    r = c.post(f"{BASE}/api/persona/refresh").json()
    print("✅ 画像更新:", r["ok"], r.get("msg", ""), "版本 v%s" % r["persona"]["version"])
    assert r["ok"]

    # 5. 状态回读
    s = c.get(f"{BASE}/api/state").json()
    print("✅ 状态: 会话数=%d mood_log=%d 画像v=%d today_doc=%s" % (
        len(s["sessions"]), len(s["persona"]["mood_log"]),
        s["persona"]["version"], bool(s["today_doc_url"])))
    assert s["persona"]["mood_log"], "情绪日志未记录"
    if not s["today_doc_url"]:
        print("   （飞书文件夹未就绪：本轮已本地记账，权限开通后自动补写）")

    # 6. 静态页
    assert "树洞" in c.get(f"{BASE}/").text
    print("✅ 静态页面正常")
    print("\n全部通过 🌳")


if __name__ == "__main__":
    sys.exit(main())
