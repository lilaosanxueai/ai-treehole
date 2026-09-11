"""容灾链路验证：弄坏主 Key → 发消息 → 确认自动尝试备用 → 恢复（真实 Key 从本机 config.json 读取）"""
import json
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8311"
REAL_KEY = json.loads(Path(__file__).parent.joinpath("config.json").read_text(encoding="utf-8"))["llm"]["api_key"]

c = httpx.Client(timeout=180)

# 1) 弄坏主 Key
print("1) 破坏主 Key …")
c.post(f"{BASE}/api/config", json={"llm": {"api_key": "sk-invalid-fallback-test"}})

# 2) 发消息，收集 SSE 事件
sid = c.post(f"{BASE}/api/session/new").json()["session"]["id"]
events, buf = [], ""
with c.stream("POST", f"{BASE}/api/chat",
              json={"session_id": sid, "role_mode": "auto",
                    "text": "容灾测试：主模型应该失败并切换备用"}) as r:
    for chunk in r.iter_text():
        buf += chunk
        while "\n\n" in buf:
            raw, buf = buf.split("\n\n", 1)
            head = raw.split("\n")[0].replace("event:", "").strip()
            events.append(head)
print("   SSE事件:", events)

# 3) 恢复真实 Key
print("3) 恢复真实 Key …")
print("  ", c.post(f"{BASE}/api/config", json={"llm": {"api_key": REAL_KEY}}).json()["msg"])
r = c.post(f"{BASE}/api/llm/check", json={}).json()
print("   复检:", r["msg"][:100])
