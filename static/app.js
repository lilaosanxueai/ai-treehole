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

/* 局域网访问令牌：?t= 一次性收取，之后放 localStorage，所有请求带头 */
(() => {
  const t = new URLSearchParams(location.search).get("t");
  if (t) {
    localStorage.setItem("treehole_token", t);
    history.replaceState(null, "", location.pathname);
  }
})();
const TOKEN = localStorage.getItem("treehole_token") || "";
const AUTH_HEADERS = TOKEN ? { "X-Treehole-Token": TOKEN } : {};

async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...AUTH_HEADERS },
    body: JSON.stringify(body || {}),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

/* ---------- 语音：朗读（TTS）与说话（STT） ---------- */
function speakText(text) {
  if (!("speechSynthesis" in window) || !text) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text.replace(/[#*`>]/g, ""));
  u.lang = "zh-CN";
  u.rate = 1;
  const zh = speechSynthesis.getVoices().find(v => /zh[-_]CN/i.test(v.lang));
  if (zh) u.voice = zh;
  speechSynthesis.speak(u);
}

let recog = null, listening = false, micBase = "";
function toggleMic() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { alert("这个浏览器不支持语音识别，试试 Edge / Chrome"); return; }
  if (listening) { recog && recog.stop(); return; }
  recog = new SR();
  recog.lang = "zh-CN";
  recog.interimResults = true;
  recog.continuous = true;
  micBase = inputEl.value ? inputEl.value.replace(/\s+$/, "") + "\n" : "";
  listening = true;
  $("btn-mic").classList.add("listening");
  $("btn-mic").title = "正在听…再点一下结束";
  recog.onresult = (e) => {
    let txt = "";
    for (let i = e.resultIndex; i < e.results.length; i++) txt += e.results[i][0].transcript;
    inputEl.value = micBase + txt;
    autosize();
  };
  recog.onend = () => {
    listening = false;
    $("btn-mic").classList.remove("listening");
    $("btn-mic").title = "点一下开始说话，再点结束";
  };
  recog.onerror = (e) => {
    listening = false;
    $("btn-mic").classList.remove("listening");
    if (e.error === "not-allowed") alert("麦克风权限被拒绝了，在浏览器地址栏锁图标里允许一下");
  };
  try { recog.start(); } catch {}
}

/* ---------- 状态加载 ---------- */
async function loadState() {
  const r = await fetch("/api/state", { headers: AUTH_HEADERS });
  if (r.status === 401) throw new Error("访问令牌缺失（请用带令牌的链接打开）");
  const s = await r.json();
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
    const r = await fetch("/api/state", { headers: AUTH_HEADERS }).then((x) => x.json()); // 顺带刷新
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
  const whispers = [
    "今天也辛苦了，把没处放的情绪都放进来吧。",
    "不管多小的事，说给我听就不算小。",
    "你不用组织语言，想到哪说到哪就好。",
    "这里没有评判，只有一棵很会听的树。",
  ];
  w.appendChild(el("div", "bubble",
    `${greet}。我是${state.friendName}，这个树洞里只有你和我会知道说过什么。\n\n${whispers[Math.floor(Math.random() * whispers.length)]}\n\n开心的、难过的、说不出口的，都可以放进来。我会认真听，也会记进你的飞书云文档——越聊，我越懂你。`));
  m.appendChild(el("div", "avatar", "🌳"));
  m.appendChild(w);
  chatEl.appendChild(m);
  scrollBottom();
  // 那年今日：偶尔浮现一条旧回忆
  fetch("/api/recall", { headers: AUTH_HEADERS })
    .then((r) => r.json())
    .then((d) => {
      const rc = d.recall || {};
      if (!rc.text) return;
      const card = el("div", "msg tree ghost");
      const wrap2 = el("div", "bubble-wrap");
      wrap2.appendChild(el("div", "bubble",
        `🗓️ 顺便想起——${rc.date} 你说过「${rc.text}」，后来怎么样了？不急着回答，想聊的时候再说。`));
      card.appendChild(el("div", "avatar", "🗓️"));
      card.appendChild(wrap2);
      chatEl.appendChild(card);
      scrollBottom();
    })
    .catch(() => {});
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
  const ops = el("div", "meta-line");
  const sp = el("span", "tag speak-btn", "🔊 读给我听");
  sp.style.cursor = "pointer";
  sp.onclick = () => speakText(text);
  ops.appendChild(sp);
  w.appendChild(ops);
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
  if (listening) { try { recog.stop(); } catch {} }  // 发送时自动结束听写

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
      headers: { "Content-Type": "application/json", ...AUTH_HEADERS },
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
          window.dispatchEvent(new CustomEvent("treehole:scan", { detail: scan }));
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
          if (localStorage.getItem("treehole_autotts") === "1") speakText(bubble.textContent);
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

/* ---------- 画像抽屉 v2：专业底座 + 大白话 ---------- */
async function loadMemories(box) {
  try {
    const d = await fetch("/api/memories", { headers: AUTH_HEADERS }).then((r) => r.json());
    box.innerHTML = "";
    const pend = d.pending || [];
    if (pend.length) {
      const h = el("div", "b5-tip", "还在惦记的约定（完成了点 ✓）：");
      h.style.marginBottom = "6px";
      box.appendChild(h);
      for (const m of pend) {
        const row = el("div", "mem-item");
        row.appendChild(el("span", "", `📌 ${m.content}${m.detail ? "（" + m.detail + "）" : ""}`));
        const done = el("span", "tag mem-done", "✓ 完成");
        done.onclick = async () => {
          await postJSON("/api/memories/done", { content: m.content });
          loadMemories(box);
        };
        row.appendChild(done);
        box.appendChild(row);
      }
    }
    const items = d.items || [];
    const others = items.filter((m) => m.type !== "promise" || m.status !== "open");
    if (others.length) {
      const chips = el("div", "chips");
      for (const m of others.slice(0, 24)) {
        const icon = { person: "👤", fact: "📌", preference: "💠", promise: "✅" }[m.type] || "·";
        chips.appendChild(el("span", "chip", `${icon} ${m.content}`)).title = (m.ts || "").slice(0, 10);
      }
      box.appendChild(chips);
    }
    if (!pend.length && !others.length) box.appendChild(el("div", "b5-tip", "聊过之后，这里会出现树洞记住的人和事。"));
  } catch (e) {
    box.textContent = "（记忆加载失败）";
  }
}

const BIG5_TIPS = {
  "神经质": "情绪的波浪幅度：分高=感受深、易被扰动；分低=稳",
  "外向性": "电量来自哪里：分高=人多的地方回血；分低=独处回血",
  "开放性": "对新东西的胃口：分高=好奇、爱尝鲜；分低=恋旧、讲实用",
  "宜人性": "待人默认温度：分高=先照顾别人；分低=先讲道理和边界",
  "尽责性": "对自己的承诺：分高=计划与完成；分低=随性、弹性大",
};
const SIGNAL_LABEL = { low_mood: "低落信号", anxiety: "焦虑信号", stress: "压力信号" };
const TREND_CN = { up: "↑ 在变重", flat: "→ 平稳", down: "↓ 在缓解" };

function big5Score(v) { return typeof v === "number" ? v : (v && typeof v.score === "number" ? v.score : null); }

function drawRadar(canvas, scores) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width = 260, H = canvas.height = 220;
  const cx = W / 2, cy = H / 2 + 6, R = 78;
  const dims = Object.keys(scores);
  if (!dims.length) return;
  const ang = i => -Math.PI / 2 + i * 2 * Math.PI / dims.length;
  ctx.clearRect(0, 0, W, H);
  const cs = getComputedStyle(document.documentElement);
  const accent = cs.getPropertyValue("--accent").trim() || "#8fd6a0";
  const line = cs.getPropertyValue("--line").trim() || "#333";
  const text = cs.getPropertyValue("--muted").trim() || "#999";
  for (let ring = 1; ring <= 4; ring++) {
    ctx.beginPath();
    for (let i = 0; i <= dims.length; i++) {
      const a = ang(i % dims.length), r = R * ring / 4;
      const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.strokeStyle = line; ctx.globalAlpha = .45; ctx.stroke(); ctx.globalAlpha = 1;
  }
  ctx.beginPath();
  dims.forEach((d, i) => {
    const a = ang(i), r = R * Math.max(4, (scores[d] || 50)) / 100;
    const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.closePath();
  ctx.fillStyle = accent; ctx.globalAlpha = .22; ctx.fill();
  ctx.globalAlpha = .9; ctx.strokeStyle = accent; ctx.lineWidth = 1.6; ctx.stroke(); ctx.globalAlpha = 1;
  ctx.font = "11px sans-serif"; ctx.fillStyle = text; ctx.textAlign = "center";
  dims.forEach((d, i) => {
    const a = ang(i);
    ctx.fillText(d, cx + Math.cos(a) * (R + 20), cy + Math.sin(a) * (R + 16) + 4);
    ctx.fillText(String(scores[d] ?? "?"), cx + Math.cos(a) * (R + 20), cy + Math.sin(a) * (R + 16) + 16);
  });
}

function drawMoodLine(canvas, logs) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width = 380, H = canvas.height = 110;
  ctx.clearRect(0, 0, W, H);
  const cs = getComputedStyle(document.documentElement);
  const accent = cs.getPropertyValue("--accent").trim() || "#8fd6a0";
  const danger = cs.getPropertyValue("--danger").trim() || "#e08563";
  const line = cs.getPropertyValue("--line").trim() || "#333";
  const dim = cs.getPropertyValue("--dim").trim() || "#666";
  const data = logs.slice(-60);
  const zero = H / 2;
  ctx.strokeStyle = line; ctx.globalAlpha = .5;
  ctx.beginPath(); ctx.moveTo(0, zero); ctx.lineTo(W, zero); ctx.stroke(); ctx.globalAlpha = 1;
  if (!data.length) {
    ctx.fillStyle = dim; ctx.font = "12px sans-serif"; ctx.textAlign = "center";
    ctx.fillText("聊过之后，这里会出现你的情绪轨迹", W / 2, H / 2 - 10);
    return;
  }
  const step = data.length > 1 ? W / (data.length - 1) : W;
  data.forEach((m, i) => {
    const x = i * step, y = zero - (m.valence || 0) / 2 * (H / 2 - 10);
    ctx.beginPath(); ctx.arc(x, y, 1.8, 0, 7);
    ctx.fillStyle = (m.valence || 0) < 0 ? danger : accent; ctx.fill();
    if (i) {
      const px = (i - 1) * step, py = zero - (data[i - 1].valence || 0) / 2 * (H / 2 - 10);
      ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(x, y);
      ctx.strokeStyle = (m.valence || 0) < 0 ? danger : accent;
      ctx.globalAlpha = .35; ctx.stroke(); ctx.globalAlpha = 1;
    }
  });
  ctx.fillStyle = dim; ctx.font = "10px sans-serif"; ctx.textAlign = "left";
  ctx.fillText("最近 " + data.length + " 条消息的情绪（上=明亮 下=低落）", 6, H - 6);
}

function renderDrawer() {
  const body = $("drawer-body");
  body.innerHTML = "";
  const p = state.persona;
  /* 长期记忆：树洞记得（无论有无画像都展示） */
  {
    const sec = el("div", "p-section");
    sec.appendChild(el("h4", "", "🧠 树洞记得"));
    const box = el("div", "", "加载中…");
    box.id = "memory-box";
    sec.appendChild(box);
    body.appendChild(sec);
    loadMemories(box);
  }
  if (!p || !p.version) {
    body.appendChild(el("div", "empty",
      "树洞还没画出你的画像。\n聊几轮之后，点上方「🔄 深度更新」试试。"));
    return;
  }

  /* 总览：白话画像 + 连续低落提示 */
  body.appendChild(el("div", "persona-summary", "「 " + (p.summary_plain || p.summary || "") + " 」"));
  const streak = (p.risk || {}).streak_low_days || 0;
  if (streak >= 3) {
    const warn = el("div", "ai-notes");
    warn.style.borderColor = "rgba(224,133,99,.5)";
    warn.textContent = `💗 连续 ${streak} 天情绪都偏低。不用急着好起来，但如果持续两周以上、影响到吃饭睡觉，建议找专业心理咨询聊聊（心理援助热线 12356）。`;
    body.appendChild(warn);
  }
  const meta = el("div", "persona-meta",
    `v${p.version} · 更新于 ${p.updated_at || "—"} · 每条结论都有对话依据，可展开查看`);
  body.appendChild(meta);

  /* 大五：雷达图 + 白话条 + 层面 */
  const b5 = p.big5 || {};
  const scores = {};
  for (const [d, v] of Object.entries(b5)) scores[d] = big5Score(v);
  if (Object.keys(scores).filter(k => scores[k] != null).length >= 3) {
    const sec = el("div", "p-section");
    const h4 = el("h4", "", "🧭 大五人格（点维度看白话解释）");
    sec.appendChild(h4);
    const wrap = el("div", "", "");
    wrap.style.cssText = "display:flex;justify-content:center";
    const radar = document.createElement("canvas");
    wrap.appendChild(radar); sec.appendChild(wrap);
    drawRadar(radar, scores);
    for (const [d, v] of Object.entries(b5)) {
      const sc = big5Score(v);
      if (sc == null) continue;
      const box = el("div", "b5-row");
      const head = el("div", "b5-head");
      const name = el("b", "", d);
      const num = el("span", "", String(sc) + (v.confidence === "低" ? "（证据还少）" : ""));
      head.append(name, num);
      const tip = el("div", "b5-tip", (v.plain || "") + (v.plain ? "" : BIG5_TIPS[d] || ""));
      box.append(head, tip);
      const facets = v.facets || {};
      if (Object.keys(facets).length) {
        const det = document.createElement("details");
        det.className = "b5-facets";
        const sum = el("summary", "", "细分层面");
        det.appendChild(sum);
        for (const [fn, fv] of Object.entries(facets)) {
          det.appendChild(el("div", "b5-tip",
            `${fn} ${fv.score} —— 依据：${fv.evidence || "—"}`));
        }
        box.appendChild(det);
      }
      sec.appendChild(box);
    }
    body.appendChild(sec);
  }

  /* 情绪信号（筛查参考） */
  const sig = p.signals || {};
  if (Object.keys(sig).length) {
    const sec = el("div", "p-section");
    sec.appendChild(el("h4", "", "🌡️ 近期情绪信号（是信号，不是诊断）"));
    for (const [k, v] of Object.entries(sig)) {
      if (!v || typeof v.score !== "number") continue;
      const row = el("div", "bar-row");
      row.appendChild(el("span", "name", SIGNAL_LABEL[k] || k));
      const track = el("div", "bar-track");
      const fill = el("div", "bar-fill");
      fill.style.width = Math.max(2, Math.min(100, v.score)) + "%";
      if (v.score >= 60) fill.style.background = "linear-gradient(90deg,#a05a3c,var(--danger))";
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el("span", "num", String(v.score)));
      sec.appendChild(row);
      if (v.trend || v.evidence) {
        sec.appendChild(el("div", "b5-tip",
          `${TREND_CN[v.trend] || ""}${v.evidence ? " · 依据：" + v.evidence : ""}`));
      }
    }
    const note = el("div", "disclaim", "这些只是从聊天里观察到的倾向，不能替代心理评估或诊断；分数高≠生病，只是最近辛苦的痕迹。");
    sec.appendChild(note);
    body.appendChild(sec);
  }

  /* 文化语境（中式底座） */
  if ((p.cultural_notes || []).length) {
    const sec = el("div", "p-section");
    sec.appendChild(el("h4", "", "🧧 文化语境（这些给 TA 的分量，树洞都懂）"));
    for (const c of p.cultural_notes) {
      const d = el("div", "trait");
      d.appendChild(el("div", "t-head bold", c.name));
      if (c.plain) d.appendChild(el("div", "t-ev", c.plain));
      if (c.evidence) d.appendChild(el("div", "t-ev", "依据：" + c.evidence));
      sec.appendChild(d);
    }
    body.appendChild(sec);
  }

  /* 反复出现的模式 */
  if ((p.patterns || []).length) {
    const sec = el("div", "p-section");
    sec.appendChild(el("h4", "", "🔁 反复出现的模式"));
    for (const pat of p.patterns) {
      const d = el("div", "trait");
      d.appendChild(el("div", "t-head bold", pat.name));
      if (pat.plain) d.appendChild(el("div", "t-ev", pat.plain));
      if (pat.evidence) d.appendChild(el("div", "t-ev", "依据：" + pat.evidence));
      sec.appendChild(d);
    }
    body.appendChild(sec);
  }

  /* 情绪时间线 */
  {
    const sec = el("div", "p-section");
    sec.appendChild(el("h4", "", "📈 情绪轨迹"));
    const c = document.createElement("canvas");
    c.style.cssText = "width:100%;max-width:420px";
    sec.appendChild(c);
    drawMoodLine(c, p.mood_log || []);
    body.appendChild(sec);
  }

  /* 核心特质（区分状态/特质） */
  if ((p.traits || []).length) {
    const sec = el("div", "p-section");
    sec.appendChild(el("h4", "", "🌱 核心特质（区分「稳定特质」与「近期状态」）"));
    for (const t of p.traits) {
      const d = el("div", "trait");
      const head = el("div", "t-head");
      head.appendChild(el("b", "", t.label));
      const tag = t.state_or_trait === "trait" ? "稳定特质" : "近期状态";
      head.appendChild(el("span", "tag" + (t.state_or_trait === "trait" ? "" : ""), tag));
      d.appendChild(head);
      if (t.evidence) d.appendChild(el("div", "t-ev", "依据：" + t.evidence));
      sec.appendChild(d);
    }
    body.appendChild(sec);
  }

  const chipSections = [
    ["💗 在意的人和事", p.care_about, ""],
    ["🪨 压力源", p.stressors, "danger"],
    ["⚡ 能量来源", p.energy_sources, "pos"],
    ["🛡️ 保护性资源", p.protective, "pos"],
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
    sec.appendChild(el("div", "ai-notes", p.ai_notes));
    body.appendChild(sec);
  }

  /* 证据台账：为什么这么说？ */
  const ledger = p.evidence_ledger || [];
  if (ledger.length) {
    const sec = el("div", "p-section");
    sec.appendChild(el("h4", "", `🔍 为什么这么说？（${ledger.length} 条观察依据）`));
    const det = document.createElement("details");
    det.appendChild(el("summary", "", "展开查看树洞的观察记录"));
    for (const o of ledger.slice(-15).reverse()) {
      const d = el("div", "ledger-item");
      d.appendChild(el("div", "", `· ${o.content}`));
      if (o.quote) d.appendChild(el("div", "t-ev", `原话：「${o.quote}」`));
      d.appendChild(el("div", "t-ev",
        `${(o.date || "").slice(5, 16)} · ${o.domain || "其他"} · ${o.state_or_trait === "trait" ? "特质线索" : "当时状态"} · 可信度${o.confidence || "低"}`));
      det.appendChild(d);
    }
    sec.appendChild(det);
    body.appendChild(sec);
  }

  const foot = el("div", "disclaim",
    "ℹ️ 以上由 AI 基于你的对话做出，供自我了解与陪伴参考；它会有偏差，也绝不构成医学诊断。如果困扰持续或加重，请信任专业人士（心理援助热线 12356）。");
  body.appendChild(foot);
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
  fetch("/api/config", { headers: AUTH_HEADERS }).then((r) => r.json()).then((c) => {
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
    $("set-think").value = c.llm.thinking_mode || "smart";
    $("set-fid").value = c.feishu.app_id || "";
    $("set-fsecret").value = c.feishu.app_secret || "";
    $("set-fsecret").placeholder = c.feishu.has_secret ? "已保存（脱敏显示，改动才覆盖）" : "";
    $("set-ftoken").value = c.feishu.folder_token || "";
    $("set-fshare").checked = !!c.feishu.auto_share_tenant;
    $("set-chatid").value = c.feishu.notify_chat_id || "";
    $("set-lan").checked = !!c.server.lan;
    $("set-token").value = c.server.access_token || "";
    $("set-name").value = c.friend_name || "树洞";
    $("set-style").value = c.friend_style || "classic";
    $("set-autotts").checked = localStorage.getItem("treehole_autotts") === "1";
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
      thinking_mode: $("set-think").value,
      backup_model: $("set-bmodel").value.trim(),
      backup_base_url: $("set-bbase").value.trim(),
      backup_api_key: $("set-bkey").value.trim(),
    },
    feishu: {
      app_id: $("set-fid").value.trim(),
      app_secret: $("set-fsecret").value.trim(),
      folder_token: $("set-ftoken").value.trim(),
      auto_share_tenant: $("set-fshare").checked,
      notify_chat_id: $("set-chatid").value.trim(),
    },
    server: {
      lan: $("set-lan").checked,
      access_token: $("set-token").value.trim(),
    },
    friend_name: $("set-name").value.trim(),
    friend_style: $("set-style").value,
  };
  localStorage.setItem("treehole_autotts", $("set-autotts").checked ? "1" : "0");
  // 空值 / 未改动的脱敏值 → 不覆盖已保存的真实密钥
  for (const sec of ["llm", "feishu", "server"]) {
    for (const k of Object.keys(body[sec])) {
      const v = body[sec][k];
      if (v === "" || (k === "api_key" && v === cfgMasked.api_key) ||
          (k === "backup_api_key" && v === cfgMasked.backup_api_key) ||
          (k === "app_secret" && v === cfgMasked.app_secret)) {
        delete body[sec][k];
      }
    }
  }
  if (!body.llm.thinking_mode) delete body.llm.thinking_mode;
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
$("btn-mic").onclick = toggleMic;
$("btn-export").onclick = async () => {
  try {
    const r = await fetch("/api/export", { headers: AUTH_HEADERS });
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "treehole-backup.json";
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) { alert("导出失败：" + e.message); }
};
$("btn-qr").onclick = async () => {
  const box = $("qr-box");
  if (!box.hidden) { box.hidden = true; return; }
  box.hidden = false;
  box.textContent = "生成中…";
  try {
    const r = await fetch("/api/qr", { headers: AUTH_HEADERS }).then((x) => x.json());
    box.innerHTML = "";
    const img = el("img");
    img.src = r.svg; img.alt = "手机连接二维码"; img.style.cssText = "width:180px;height:180px;display:block;margin:8px auto;background:#fff;border-radius:10px;padding:6px";
    box.appendChild(img);
    const tip = el("div", "tip", `手机扫码或访问：${r.url}`);
    tip.style.cssText = "font-size:12px;color:var(--dim);text-align:center;word-break:break-all";
    box.appendChild(tip);
  } catch (e) {
    box.textContent = "二维码获取失败：" + e.message;
  }
};

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
