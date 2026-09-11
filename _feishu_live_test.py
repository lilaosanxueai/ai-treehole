"""真实飞书链路验证：token → 建文档（文件夹不可用时自动降级到应用空间）→ 追加块 → 回收站清理

只使用 config.json 里的自建应用凭据，不动用户已有数据。
"""
import json
from pathlib import Path

import feishu

cfg = json.loads(Path(__file__).parent.joinpath("config.json").read_text(encoding="utf-8"))
fs = feishu.Feishu(cfg["feishu"])

print("[1] tenant_access_token …")
tok = fs._get_token()
print("    ✅ token:", tok[:12] + "…")

print("[2] 文件夹 …")
try:
    folder = fs.ensure_folder()
    print("    ✅ folder:", folder)
except Exception as e:
    folder = None
    print("    ⚠️ 文件夹不可用（drive:drive 未开通？），文档将建在应用空间 —", str(e)[:100])

print("[3] 建测试文档 …")
doc = fs.create_doc("🌳 树洞 · 连通性测试（可删除）")
print("    ✅ doc:", doc["token"], doc["url"])

print("[4] 追加对话块 …")
blocks = feishu.turn_blocks(
    1, "12:00",
    "今天被领导当众批评了，项目延期的锅全扣我头上，\n真的很难受。",
    "当众被说，这口气很难咽。你愿意跟我说说当时的场景吗？",
    "　· 情绪 委屈 72 · 话题 工作、领导 · 角色 安抚者",
)
blocks = feishu.session_head_blocks("12:00", "被领导批评了") + blocks
blocks += feishu.summary_blocks("12:05", "- TA 因项目延期被领导当众批评，感到委屈\n- 聊到了和领导的信任问题")
blocks += feishu.persona_snapshot_blocks(1, "09-11 12:06", ["一句话画像", "# 核心特质", "报喜不报忧（78）"])
fs.append_blocks(doc["token"], blocks)
print("    ✅ 已写入 %d 块（对话/小结/画像快照 三类块型）" % len(blocks))

print("[5] 清理：把测试文档移入回收站 …")
try:
    fs._api("DELETE", f"/drive/v1/files/{doc['token']}", params={"type": "docx"})
    print("    ✅ 已移入回收站")
except Exception as e:
    print("    ⚠️ 自动清理失败（文档在应用空间里，不可见，无碍）：", str(e)[:100])

print("\n飞书文档读写链路验证完成 🌳", doc["url"])
