/* 树洞挚友 · 前端逻辑 */
"use strict";

const $ = (id) => document.getElementById(id);
const chatEl = $("chat");
const inputEl = $("input");

const state = {
  sessionId: localStorage.getItem("treehole_sid") || "",
  persona: null,
  sessions: [],
  roles: {},
  roleMode: "auto",
  hotlines: [],
  streaming: false,
  friendName: "树洞",
};

/* ---------- 基础工具 ---------- */
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}
function scrollBottom() { chatEl.scrollTop = chatEl.scrollHeight; }

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

/* ---------- 状态加载 ---------- */
async function loadState() {
  const s = await fetch("/api/state").then((r) => r.json());
  state.persona = s.persona;
  state.sessions = s.sessions || [];
  state.roles = s.roles || {};
  state.hotlines = s.hotlines || [];
  state.friendName = s.friend_name || "树洞";
  $("brand-name").textContent = state.friendName + "挚友";

  setPill($("pill-feishu"), s.feishu_ready, "☁️ 飞书", "☁️ 飞书未配置");
  setPill($("pill-llm"), s.llm_ready, "🧠 " + (s.model || "模型"), "🧠 模型未配置");
  if (s.today_doc_url) {
    $("doc-link").hidden = false;
    $("doc-link").href = s.today_doc_url;
  } else {
    $("doc-link").hidden = true;
  }
  renderRoles();
  renderMoodSpark();
  renderDrawer();
  return s;
}

function setPill(node, ok, okText, badText) {
  node.textContent = ok ? okText + " ✓" : badText;
  node.classList.toggle("ok", !!ok);
  node.classList.toggle("bad", !ok);
}

/* ---------- 会话 ---------- */
async function ensureSession() {
  if (state.sessionId) {
    const r = await fetch("/api/state").then((x) => x.json()); // 顺带刷新
    const exists = r.sessions.some((s) => s.id === state.sessionId);
    if (exists) return loadSession(state.sessionId);
  }
  return newSession();
}

async function newSession() {
  const data = await postJSON("/api/session/new");
  state.sessionId = data.session.id;
  localStorage.setItem("treehole_sid", state.sessionId);
  chatEl.innerHTML = "";
  welcome();
  return data.session;
}

async function loadSession(sid) {
  try {
    const data = await postJSON("/api/session/load", { session_id: sid });
    state.sessionId = sid;
    localStorage.setItem("treehole_sid", sid);
    chatEl.innerHTML = "";
    const sess = data.session;
    if (!sess.messages || !sess.messages.length) { welcome(); return; }
    for (const m of sess.messages) {
      if (m.role === "user") renderUserMsg(m.content, m.scan);
      else renderTreeMsg(m.content);
    }
    if (sess.summary) {
      const d = el("div", "day-divider", "📌 本次小结");
      chatEl.appendChild(d);
      const s = el("div", "msg tree ghost");
      const w = el("div", "bubble-wrap");
      w.appendChild(el("div", "bubble", sess.summary));
      s.appendChild(el("div", "avatar", "🌳")); s.appendChild(w);
      chatEl.appendChild(s);
    }
    scrollBottom();
  } catch (e) {
    console.error(e);
    newSession();
  }
}

function welcome() {
  const m = el("div", "msg tree ghost");
  const w = el("div", "bubble-wrap");
  const now = new Date().getHours();
  const greet = now < 6 ? "夜这么深了还没睡呀" : now < 11 ? "早上好" : now < 14 ? "中午好" : now < 18 ? "下午好" : now < 23 ? "晚上好" : "夜深了";
  w.appendChild(el("div", "bubble",
    `${greet}。我是${state.friendName}，这个树洞里只有你和我会知道说过什么。\n\n开心的、难过的、说不出口的，都可以放进来。我会认真听，也会记进你的飞书云文档——越聊，我越懂你。`));
  m.appendChild(el("div", "avatar", "🌳"));
  m.appendChild(w);
  chatEl.appendChild(m);
  scrollBottom();
}

/* ---------- 消息渲染 ---------- */
function renderUserMsg(text, scan) {
  const m = el("div", "msg user");
  const w = el("div", "bubble-wrap");
  w.appendChild(el("div", "bubble", text));
  if (scan) w.appendChild(metaLine(scan));
  m.appendChild(el("div", "avatar", "🙋"));
  m.appendChild(w);
  chatEl.appendChild(m);
  scrollBottom();
  return m;
}

function metaLine(scan) {
  const line = el("div", "meta-line");
  const neg = (scan.valence || 0) < 0;
  const chip = el("span", "tag " + (neg ? "neg" : (scan.valence || 0) > 0 ? "pos" : ""),
    `${scan.emotion} ${scan.intensity}`);
  line.appendChild(chip);
  if (scan.topics && scan.topics.length) line.appendChild(el("span", "tag", scan.topics.join(" · ")));
  const roleName = (state.roles[scan.role] || {}).name || scan.role;
  line.appendChild(el("span", "tag", roleName));
  if (scan.one_line) {
    const s = el("span", "", scan.one_line);
    s.style.color = "var(--dim)";
    line.appendChild(s);
  }
  return line;
}

function renderTreeMsg(text) {
  const m = el("div", "msg tree");
  const w = el("div", "bubble-wrap");
  w.appendChild(el("div", "bubble", text));
  m.appendChild(el("div", "avatar", "🌳"));
  m.appendChild(w);
  chatEl.appendChild(m);
  scrollBottom();
  return m;
}

function renderRiskCard() {
  const card = el("div", "risk-card");
  card.appendChild(el("div", "", "🛡️ 树洞很在意你。如果你此刻很难很难，请一定让真实的人接住你——"));
  for (const h of state.hotlines) {
    const row = el("div");
    row.appendChild(document.createTextNode(`${h.name}：`));
    row.appendChild(el("span", "tel", h.tel));
    card.appendChild(row);
  }
  card.appendChild(el("div", "", "这些热线都是免费的、24 小时的。你不是负担。"));
  chatEl.appendChild(card);
  scrollBottom();
}

/* ---------- 角色选择 ---------- */
function renderRoles() {
  const box = $("roles");
  box.innerHTML = "";
  const order = ["auto", "listener", "talker", "sharer", "soother"];
  const hints = {
    auto: "按你的状态自动切换",
    listener: "只想被听见的时候",
    talker: "想平等聊聊天",
    sharer: "想听观点和故事",
    soother: "需要先被接住",
  };
  for (const key of order) {
    const r = state.roles[key];
    if (!r) continue;
    const chip = el("span", "role-chip" + (state.roleMode === key ? " active" : ""), r.label);
    chip.title = hints[key] || "";
    chip.onclick = () => {
      state.roleMode = key;
      renderRoles();
    };
    box.appendChild(chip);
  }
}

/* ---------- 情绪条 ---------- */
function setMoodNow(scan, roleLabel) {
  const box = $("mood-now");
  box.innerHTML = "";
  box.appendChild(el("span", "mood-label", "此刻"));
  const neg = (scan.valence || 0) < 0;
  box.appendChild(el("span", "mood-chip " + (neg ? "neg" : (scan.valence || 0) > 0 ? "pos" : ""),
    `${scan.emotion} ${scan.intensity}`));
  if (roleLabel) box.appendChild(el("span", "mood-chip", roleLabel));
  if (scan.one_line) {
    const s = el("span", "", scan.one_line);
    s.style.cssText = "color:var(--dim);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:380px";
    box.appendChild(s);
  }
}

function renderMoodSpark() {
  const box = $("mood-spark");
  if (!box) return;
  box.innerHTML = "";
  const log = ((state.persona || {}).mood_log || []).slice(-30);
  for (const m of log) {
    const bar = el("span", "spark-bar");
    const h = 4 + Math.round((m.intensity / 100) * 22);
    bar.style.height = h + "px";
    const v = m.valence || 0;
    bar.style.background = v < 0 ? "#d98a6a" : v > 0 ? "#8fd6a0" : "#7a8d7f";
    bar.title = `${m.ts} ${m.emotion} ${m.intensity}`;
    box.appendChild(bar);
  }
}

/* ---------- 发送 & SSE ---------- */
async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || state.streaming) return;
  state.streaming = true;
  $("send").disabled = true;
  inputEl.value = "";
  autosize();

  if (!state.sessionId) await newSession();
  const userMsgEl = renderUserMsg(text, null);
  const userWrapEl = userMsgEl.querySelector(".bubble-wrap");

  // 树洞正在听
  const treeMsg = el("div", "msg tree");
  treeMsg.appendChild(el("div", "avatar", "🌳"));
  const wrap = el("div", "bubble-wrap");
  const bubble = el("div", "bubble");
  const typing = el("span", "typing");
  typing.appendChild(el("i")); typing.appendChild(el("i")); typing.appendChild(el("i"));
  bubble.appendChild(typing);
  wrap.appendChild(bubble);
  treeMsg.appendChild(wrap);
  chatEl.appendChild(treeMsg);
  scrollBottom();

  let scan = null;
  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, text, role_mode: state.roleMode }),
    });
    if (!resp.ok || !resp.body) throw new Error("HTTP " + resp.status);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let gotAny = false;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const evt = parseSSE(chunk);
        if (!evt) continue;
        if (evt.event === "scan") {
          scan = evt.data.scan;
          setMoodNow(scan, evt.data.role_label);
          // 给已渲染的用户消息补情绪标注
          if (scan && userWrapEl && !userWrapEl.querySelector(".meta-line")) {
            userWrapEl.appendChild(metaLine(scan));
          }
        } else if (evt.event === "delta") {
          if (!gotAny) { bubble.innerHTML = ""; gotAny = true; }
          bubble.appendChild(document.createTextNode(evt.data.text));
          scrollBottom();
        } else if (evt.event === "done") {
          if (evt.data.doc_url) {
            $("doc-link").hidden = false;
            $("doc-link").href = evt.data.doc_url;
          }
          if (evt.data.risk === "high" || (scan && scan.risk === "high")) renderRiskCard();
        } else if (evt.event === "error") {
          bubble.innerHTML = "";
          bubble.appendChild(document.createTextNode("（" + evt.data.msg + "）"));
          bubble.parentElement.parentElement.classList.add("ghost");
        }
      }
    }
  } catch (e) {
    bubble.innerHTML = "";
    bubble.appendChild(document.createTextNode("（连接树洞失败：" + e.message + "）"));
  } finally {
    state.streaming = false;
    $("send").disabled = false;
    inputEl.focus();
    // 后台可能更新了画像/情绪，稍后刷新
    setTimeout(() => { loadState(); }, 1500);
  }
}

function parseSSE(chunk) {
  let event = "message";
  let data = "";
  for (const line of chunk.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return null;
  try { return { event, data: JSON.parse(data) }; } catch { return null; }
}

/* ---------- 画像抽屉 ---------- */
function renderDrawer() {
  const body = $("drawer-body");
  body.innerHTML = "";
  const p = state.persona;
  if (!p || !p.version) {
    body.appendChild(el("div", "empty",
      "树洞还没画出你的画像。\n聊几轮之后，点上方「🔄 深度更新」试试。"));
    return;
  }
  body.appendChild(el("div", "persona-summary", "「 " + (p.summary || "") + " 」"));
  body.appendChild(el("div", "persona-meta",
    `v${p.version} · 更新于 ${p.updated_at || "—"} · 由对话记录自动分析`));

  if (p.big5 && Object.keys(p.big5).length) {
    const sec = el("div", "p-section");
    sec.appendChild(el("h4", "", "🧭 大五人格估分"));
    for (const [k, v] of Object.entries(p.big5)) {
      const row = el("div", "bar-row");
      row.appendChild(el("span", "name", k));
      const track = el("div", "bar-track");
      const fill = el("div", "bar-fill");
      fill.style.width = Math.max(2, Math.min(100, Number(v) || 0)) + "%";
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el("span", "num", String(v)));
      sec.appendChild(row);
    }
    body.appendChild(sec);
  }
  if ((p.traits || []).length) {
    const sec = el("div", "p-section");
    sec.appendChild(el("h4", "", "🌱 核心特质"));
    for (const t of p.traits) {
      const d = el("div", "trait");
      const head = el("div", "t-head");
      head.appendChild(el("b", "", t.label));
      head.appendChild(el("span", "", (t.score != null ? t.score : "") + (t.evidence ? "" : "")));
      d.appendChild(head);
      if (t.evidence) d.appendChild(el("div", "t-ev", "依据：" + t.evidence));
      sec.appendChild(d);
    }
    body.appendChild(sec);
  }
  if (p.emotional_baseline) {
    const sec = el("div", "p-section");
    sec.appendChild(el("h4", "", "🌡️ 情绪基调"));
    sec.appendChild(el("div", "", p.emotional_baseline)).style.cssText = "font-size:13.5px;line-height:1.8;color:var(--muted)";
    body.appendChild(sec);
  }
  const chipSections = [
    ["💗 在意的人和事", p.care_about, ""],
    ["🪨 压力源", p.stressors, "danger"],
    ["⚡ 能量来源", p.energy_sources, "pos"],
    ["💬 偏好的沟通方式", p.communication_prefs, "warm"],
  ];
  for (const [title, arr, cls] of chipSections) {
    if (!(arr || []).length) continue;
    const sec = el("div", "p-section");
    sec.appendChild(el("h4", "", title));
    const chips = el("div", "chips");
    for (const c of arr) chips.appendChild(el("span", "chip " + cls, String(c)));
    sec.appendChild(chips);
    body.appendChild(sec);
  }
  if ((p.memorable_quotes || []).length) {
    const sec = el("div", "p-section");
    sec.appendChild(el("h4", "", "🗝️ 你说过的、树洞记住了的话"));
    for (const q of p.memorable_quotes) sec.appendChild(el("div", "quote", "“" + q + "”"));
    body.appendChild(sec);
  }
  if (p.ai_notes) {
    const sec = el("div", "p-section");
    sec.appendChild(el("h4", "", "📝 树洞的陪伴备忘"));
    const n = el("div", "ai-notes", p.ai_notes);
    sec.appendChild(n);
    body.appendChild(sec);
  }
}

async function refreshPersona() {
  const btn = $("btn-refresh-persona");
  btn.disabled = true;
  btn.textContent = "🔄 分析中…";
  try {
    const r = await postJSON("/api/persona/refresh");
    if (r.ok) {
      state.persona = r.persona;
      renderDrawer();
      btn.textContent = "✅ 已更新";
    } else {
      btn.textContent = "❌ " + (r.msg || "失败");
    }
  } catch (e) {
    btn.textContent = "❌ " + e.message;
  } finally {
    setTimeout(() => { btn.disabled = false; btn.textContent = "🔄 深度更新"; }, 2000);
  }
}

/* ---------- 心事列表 ---------- */
function renderHistory() {
  const body = $("history-body");
  body.innerHTML = "";
  if (!state.sessions.length) {
    body.appendChild(el("div", "empty", "还没有心事记录。"));
    return;
  }
  for (const s of state.sessions) {
    if (s.id === state.sessionId && !s.summary) continue;
    const item = el("div", "session-item");
    const head = el("div", "s-title");
    head.appendChild(el("span", "", (s.title || "树洞时刻")));
    head.appendChild(el("span", "date", `${(s.started || "").slice(5, 16)} · ${s.turns}轮`));
    item.appendChild(head);
    if (s.summary) item.appendChild(el("div", "s-sum", s.summary));
    if (s.doc_url) {
      const a = el("a", "", "📄 飞书记录 ↗");
      a.href = s.doc_url; a.target = "_blank"; a.rel = "noopener";
      a.style.cssText = "font-size:11.5px;color:var(--warm);text-decoration:none;display:inline-block;margin-top:4px";
      item.appendChild(a);
    }
    item.onclick = (e) => {
      if (e.target.tagName === "A") return;
      loadSession(s.id);
      toggleHistory(false);
    };
    body.appendChild(item);
  }
}

/* ---------- 设置 ---------- */
let cfgMasked = { api_key: "", backup_api_key: "", app_secret: "" };

function openSettings() {
  fetch("/api/config").then((r) => r.json()).then((c) => {
    cfgMasked = {
      api_key: c.llm.api_key,
      backup_api_key: c.llm.backup_api_key,
      app_secret: c.feishu.app_secret,
    };
    $("set-key").value = c.llm.api_key || "";
    $("set-key").placeholder = c.llm.has_key ? "已保存（脱敏显示，改动才覆盖）" : "sk-…";
    $("set-base").value = c.llm.base_url || "";
    $("set-model").value = c.llm.model || "";
    $("set-fast").value = c.llm.fast_model || "";
    $("set-bmodel").value = c.llm.backup_model || "";
    $("set-bbase").value = c.llm.backup_base_url || "";
    $("set-bkey").value = c.llm.backup_api_key || "";
    $("set-bkey").placeholder = c.llm.has_backup_key ? "已保存（脱敏显示，改动才覆盖）" : "sk-…";
    $("set-fid").value = c.feishu.app_id || "";
    $("set-fsecret").value = c.feishu.app_secret || "";
    $("set-fsecret").placeholder = c.feishu.has_secret ? "已保存（脱敏显示，改动才覆盖）" : "";
    $("set-ftoken").value = c.feishu.folder_token || "";
    $("set-fshare").checked = !!c.feishu.auto_share_tenant;
    $("set-name").value = c.friend_name || "树洞";
  });
  $("modal-settings").hidden = false;
}

async function saveSettings() {
  const body = {
    llm: {
      api_key: $("set-key").value.trim(),
      base_url: $("set-base").value.trim(),
      model: $("set-model").value.trim(),
      fast_model: $("set-fast").value.trim(),
      backup_model: $("set-bmodel").value.trim(),
      backup_base_url: $("set-bbase").value.trim(),
      backup_api_key: $("set-bkey").value.trim(),
    },
    feishu: {
      app_id: $("set-fid").value.trim(),
      app_secret: $("set-fsecret").value.trim(),
      folder_token: $("set-ftoken").value.trim(),
      auto_share_tenant: $("set-fshare").checked,
    },
    friend_name: $("set-name").value.trim(),
  };
  // 空值 / 未改动的脱敏值 → 不覆盖已保存的真实密钥
  for (const sec of ["llm", "feishu"]) {
    for (const k of Object.keys(body[sec])) {
      const v = body[sec][k];
      if (v === "" || (k === "api_key" && v === cfgMasked.api_key) ||
          (k === "backup_api_key" && v === cfgMasked.backup_api_key) ||
          (k === "app_secret" && v === cfgMasked.app_secret)) {
        delete body[sec][k];
      }
    }
  }
  const r = await postJSON("/api/config", body);
  alert(r.msg || "已保存");
  $("modal-settings").hidden = true;
  loadState();
}

async function testEndpoint(url, resultEl) {
  resultEl.textContent = "测试中…";
  try {
    const r = await postJSON(url);
    resultEl.textContent = (r.ok ? "✅ " : "❌ ") + (r.msg || "");
    resultEl.style.color = r.ok ? "var(--accent)" : "var(--danger)";
  } catch (e) {
    resultEl.textContent = "❌ " + e.message;
    resultEl.style.color = "var(--danger)";
  }
}

/* ---------- 抽屉开关 ---------- */
function toggleDrawer(open) {
  $("drawer").classList.toggle("open", open);
  $("drawer-mask").hidden = !open;
}
function toggleHistory(open) {
  renderHistory();
  $("history").classList.toggle("open", open);
  $("drawer-mask").hidden = !open;
  if ($("drawer").classList.contains("open") && open) toggleDrawer(false);
}

/* ---------- 输入框 ---------- */
function autosize() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 160) + "px";
}

/* ---------- 事件绑定 ---------- */
$("send").onclick = sendMessage;
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
inputEl.addEventListener("input", autosize);
$("btn-new").onclick = newSession;
$("btn-persona").onclick = () => toggleDrawer(true);
$("btn-close-drawer").onclick = () => toggleDrawer(false);
$("btn-history").onclick = () => toggleHistory(true);
$("btn-close-history").onclick = () => toggleHistory(false);
$("drawer-mask").onclick = () => { toggleDrawer(false); toggleHistory(false); };
$("btn-settings").onclick = openSettings;
$("btn-close-settings").onclick = () => ($("modal-settings").hidden = true);
$("btn-save-settings").onclick = saveSettings;
$("btn-test-llm").onclick = () => testEndpoint("/api/llm/check", $("test-llm-result"));
$("btn-test-feishu").onclick = () => testEndpoint("/api/feishu/check", $("test-feishu-result"));
$("btn-refresh-persona").onclick = refreshPersona;

/* ---------- 启动 ---------- */
(async function init() {
  try {
    await loadState();
    await ensureSession();
  } catch (e) {
    chatEl.appendChild(el("div", "empty", "树洞还没醒来：服务未启动或出错（" + e.message + "）"));
  }
  inputEl.focus();
})();
