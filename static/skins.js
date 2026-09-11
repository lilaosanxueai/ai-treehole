/* 树洞挚友 · 皮肤与氛围系统
   7 套治愈系皮肤 + 粒子引擎（canvas）+ 高自由度调节 + 随时间自动换肤 + 情绪微光 */
"use strict";

/* ---------- 皮肤定义 ---------- */
const SKINS = [
  { id: "forest", icon: "🌳", name: "深夜森林", desc: "萤火虫陪你说话", fx: ["fireflies"] },
  { id: "ocean",  icon: "🐋", name: "海底鲸歌", desc: "气泡缓缓上升", fx: ["bubbles"] },
  { id: "sakura", icon: "🌸", name: "樱花信笺", desc: "落花的浅色午后", fx: ["petals"] },
  { id: "starry", icon: "🌙", name: "星空电台", desc: "星光与流星", fx: ["stars"] },
  { id: "ember",  icon: "🔥", name: "暖炉小屋", desc: "炉火与浮尘", fx: ["embers", "motes"] },
  { id: "valley", icon: "🍃", name: "青苔溪谷", desc: "薄雾与落叶", fx: ["mist", "leaves"] },
  { id: "paper",  icon: "☀️", name: "纸间日光", desc: "干净的光尘", fx: ["motes"] },
];

/* 随时间自动选肤（治愈节律） */
function autoSkinByHour(h) {
  if (h >= 5 && h < 10) return "paper";
  if (h < 14) return "valley";
  if (h < 18) return "sakura";
  if (h < 22) return "starry";
  return "forest";
}

/* ---------- 状态 ---------- */
const DEFAULT_SKIN_STATE = {
  skin: "forest", auto: false,
  particles: true, density: 1,       // 0 少 1 中 2 多
  anims: true, brightness: 50, zoom: 1,
  accent: "", customCSS: "",
};
let skinState = (() => {
  try { return { ...DEFAULT_SKIN_STATE, ...JSON.parse(localStorage.getItem("treehole_skin") || "{}") }; }
  catch { return { ...DEFAULT_SKIN_STATE }; }
})();
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
if (reducedMotion) { skinState.particles = false; skinState.anims = false; }

function saveSkinState() { localStorage.setItem("treehole_skin", JSON.stringify(skinState)); }
function currentSkinId() {
  return skinState.auto ? autoSkinByHour(new Date().getHours()) : skinState.skin;
}
function currentSkin() { return SKINS.find(s => s.id === currentSkinId()) || SKINS[0]; }

/* ---------- 应用皮肤 ---------- */
function applySkin() {
  const id = currentSkinId();
  document.documentElement.setAttribute("data-skin", id);

  // 动效
  document.body.classList.toggle("anims-on", skinState.anims);

  // 亮度纱：50 为中性，低了变暗、高了微亮
  const veil = document.getElementById("veil-bright");
  const d = skinState.brightness - 50;
  if (veil) {
    if (d < 0) { veil.style.setProperty("--veil-color", "#000"); veil.style.setProperty("--veil", String(-d / 100 * 0.5)); }
    else { veil.style.setProperty("--veil-color", "#fff"); veil.style.setProperty("--veil", String(d / 100 * 0.18)); }
  }

  // 缩放
  document.documentElement.style.setProperty("--zoom", String(skinState.zoom));

  // 强调色覆盖
  document.documentElement.style.removeProperty("--accent");
  document.documentElement.style.removeProperty("--accent-dim");
  if (skinState.accent) {
    document.documentElement.style.setProperty("--accent", skinState.accent);
    document.documentElement.style.setProperty("--accent-dim", `color-mix(in srgb, ${skinState.accent} 55%, #000)`);
  }

  // 自定义 CSS
  let tag = document.getElementById("custom-css");
  if (!tag) { tag = document.createElement("style"); tag.id = "custom-css"; document.head.appendChild(tag); }
  tag.textContent = skinState.customCSS || "";

  startFx(currentSkin().fx);
  renderSkinCards();
  saveSkinState();
}

/* ============================================================
   粒子引擎（单 canvas，多系统叠加）
   ============================================================ */
const FX = {
  canvas: null, ctx: null, parts: [], systems: [],
  raf: 0, last: 0, w: 0, h: 0,
  shootTimer: 0,
};

function densityFactor() { return [0.45, 1, 1.7][skinState.density] || 1; }

function fxResize() {
  if (!FX.canvas) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  FX.w = window.innerWidth; FX.h = window.innerHeight;
  FX.canvas.width = FX.w * dpr; FX.canvas.height = FX.h * dpr;
  FX.canvas.style.width = FX.w + "px"; FX.canvas.style.height = FX.h + "px";
  FX.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

const R = (a, b) => a + Math.random() * (b - a);

const FX_SYSTEMS = {
  fireflies(n) {
    for (let i = 0; i < n; i++) FX.parts.push({
      t: "ff", x: R(0, FX.w), y: R(0, FX.h), r: R(1.2, 2.6),
      ph: R(0, Math.PI * 2), sp: R(.2, .6), drift: R(-.15, .15), amp: R(6, 18),
    });
  },
  bubbles(n) {
    for (let i = 0; i < n; i++) FX.parts.push({
      t: "bu", x: R(0, FX.w), y: R(0, FX.h), r: R(2, 7),
      vy: R(.25, .8), ph: R(0, Math.PI * 2),
    });
  },
  petals(n) {
    for (let i = 0; i < n; i++) FX.parts.push({
      t: "pe", x: R(0, FX.w), y: R(-FX.h, FX.h), s: R(4, 8),
      vy: R(.4, 1.1), rot: R(0, Math.PI * 2), vr: R(-.02, .02),
      ph: R(0, Math.PI * 2), hue: Math.random() > .5,
    });
  },
  stars(n) {
    for (let i = 0; i < n; i++) FX.parts.push({
      t: "st", x: R(0, FX.w), y: R(0, FX.h * .85), r: R(.5, 1.6),
      ph: R(0, Math.PI * 2), sp: R(.5, 1.8),
    });
  },
  embers(n) {
    for (let i = 0; i < n; i++) FX.parts.push({
      t: "em", x: R(0, FX.w), y: R(0, FX.h), r: R(.8, 2.2),
      vy: R(.3, .9), ph: R(0, Math.PI * 2), drift: R(-.2, .2),
    });
  },
  motes(n) {
    for (let i = 0; i < n; i++) FX.parts.push({
      t: "mo", x: R(0, FX.w), y: R(0, FX.h), r: R(.6, 1.4),
      vx: R(-.12, .12), vy: R(-.08, .08), a: R(.06, .2),
    });
  },
  leaves(n) {
    for (let i = 0; i < n; i++) FX.parts.push({
      t: "le", x: R(0, FX.w), y: R(-FX.h, FX.h), s: R(5, 10),
      vy: R(.25, .7), rot: R(0, Math.PI * 2), vr: R(-.015, .015), ph: R(0, Math.PI * 2),
    });
  },
  mist(n) {
    for (let i = 0; i < n; i++) FX.parts.push({
      t: "mi", x: R(0, FX.w), y: R(FX.h * .25, FX.h), r: R(120, 260),
      vx: R(.08, .25) * (Math.random() > .5 ? 1 : -1), a: R(.02, .045),
    });
  },
};
const FX_BASE = { fireflies: 26, bubbles: 20, petals: 18, stars: 70, embers: 22, motes: 26, leaves: 12, mist: 6 };

function fxStep(dt) {
  const { ctx, w, h } = FX;
  ctx.clearRect(0, 0, w, h);
  const skin = currentSkin();
  const cs = getComputedStyle(document.documentElement);
  const accent = cs.getPropertyValue("--accent").trim() || "#8fd6a0";

  for (const p of FX.parts) {
    switch (p.t) {
      case "ff": {
        p.ph += .016 * p.sp * 10 * dt; p.x += p.drift * dt * 60; p.y += Math.sin(p.ph) * .3;
        if (p.x < -10) p.x = w + 10; if (p.x > w + 10) p.x = -10;
        const a = .25 + .55 * (Math.sin(p.ph * .7) * .5 + .5);
        const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.r * 5);
        g.addColorStop(0, `rgba(190,240,170,${a})`); g.addColorStop(.4, `rgba(150,220,140,${a * .35})`); g.addColorStop(1, "transparent");
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p.x, p.y, p.r * 5, 0, 7); ctx.fill();
        break;
      }
      case "bu": {
        p.y -= p.vy * dt * 60; p.x += Math.sin(p.ph += .02 * dt * 60) * .4;
        if (p.y < -12) { p.y = h + 12; p.x = R(0, w); }
        ctx.strokeStyle = `rgba(180,225,245,.28)`; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 7); ctx.stroke();
        ctx.strokeStyle = "rgba(255,255,255,.35)";
        ctx.beginPath(); ctx.arc(p.x - p.r * .3, p.y - p.r * .3, p.r * .45, Math.PI * .9, Math.PI * 1.5); ctx.stroke();
        break;
      }
      case "pe": {
        p.y += p.vy * dt * 60; p.x += Math.sin(p.ph += .014 * dt * 60) * .9; p.rot += p.vr * dt * 60;
        if (p.y > h + 14) { p.y = -14; p.x = R(0, w); }
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot);
        ctx.fillStyle = p.hue ? "rgba(240,170,190,.55)" : "rgba(248,200,214,.45)";
        ctx.beginPath(); ctx.ellipse(0, 0, p.s, p.s * .55, 0, 0, 7); ctx.fill();
        ctx.restore();
        break;
      }
      case "st": {
        const a = .15 + .7 * (Math.sin(p.ph += .02 * p.sp * dt * 60) * .5 + .5);
        ctx.fillStyle = `rgba(235,232,255,${a})`;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 7); ctx.fill();
        if (p.r > 1.2) {
          ctx.strokeStyle = `rgba(235,232,255,${a * .4})`; ctx.lineWidth = .6;
          ctx.beginPath(); ctx.moveTo(p.x - p.r * 3, p.y); ctx.lineTo(p.x + p.r * 3, p.y);
          ctx.moveTo(p.x, p.y - p.r * 3); ctx.lineTo(p.x, p.y + p.r * 3); ctx.stroke();
        }
        break;
      }
      case "em": {
        p.y -= p.vy * dt * 60; p.x += (p.drift + Math.sin(p.ph += .05 * dt * 60) * .2) * dt * 60;
        if (p.y < -8) { p.y = h + 8; p.x = R(0, w); }
        const flick = .4 + .6 * (Math.sin(p.ph * 2) * .5 + .5);
        const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.r * 6);
        g.addColorStop(0, `rgba(255,190,110,${.7 * flick})`); g.addColorStop(.5, `rgba(230,120,50,${.25 * flick})`); g.addColorStop(1, "transparent");
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p.x, p.y, p.r * 6, 0, 7); ctx.fill();
        break;
      }
      case "mo": {
        p.x += p.vx * dt * 60; p.y += p.vy * dt * 60;
        if (p.x < 0 || p.x > w) p.vx *= -1; if (p.y < 0 || p.y > h) p.vy *= -1;
        ctx.fillStyle = `rgba(255,250,220,${p.a})`;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 7); ctx.fill();
        break;
      }
      case "le": {
        p.y += p.vy * dt * 60; p.x += Math.sin(p.ph += .008 * dt * 60) * .5; p.rot += p.vr * dt * 60;
        if (p.y > h + 14) { p.y = -14; p.x = R(0, w); }
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot);
        ctx.fillStyle = "rgba(150,200,150,.35)";
        ctx.beginPath(); ctx.ellipse(0, 0, p.s, p.s * .42, 0, 0, 7); ctx.fill();
        ctx.strokeStyle = "rgba(150,200,150,.3)"; ctx.lineWidth = .7;
        ctx.beginPath(); ctx.moveTo(-p.s, 0); ctx.lineTo(p.s, 0); ctx.stroke();
        ctx.restore();
        break;
      }
      case "mi": {
        p.x += p.vx * dt * 60;
        if (p.x < -p.r) p.x = w + p.r; if (p.x > w + p.r) p.x = -p.r;
        const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.r);
        g.addColorStop(0, `rgba(200,230,215,${p.a})`); g.addColorStop(1, "transparent");
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 7); ctx.fill();
        break;
      }
      case "shoot": {
        p.x += p.vx * dt * 60; p.y += p.vy * dt * 60; p.life -= dt;
        const a = Math.max(0, p.life / p.max);
        const g = ctx.createLinearGradient(p.x, p.y, p.x - p.vx * 16, p.y - p.vy * 16);
        g.addColorStop(0, `rgba(255,255,255,${.8 * a})`); g.addColorStop(1, "transparent");
        ctx.strokeStyle = g; ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(p.x - p.vx * 16, p.y - p.vy * 16); ctx.stroke();
        break;
      }
    }
  }

  // 流星（星空皮肤专属）
  if (skin.fx.includes("stars")) {
    FX.shootTimer -= dt;
    if (FX.shootTimer <= 0) {
      FX.shootTimer = R(5, 13);
      FX.parts.push({ t: "shoot", x: R(FX.w * .3, FX.w), y: R(0, FX.h * .3), vx: -7.5, vy: 3.4, life: 1.1, max: 1.1 });
    }
  }
  FX.parts = FX.parts.filter(p => p.t !== "shoot" || p.life > 0);

  // 强调色微光晕（呼吸）
  const glowA = .035 + .02 * Math.sin(performance.now() / 2600);
  const g2 = ctx.createRadialGradient(FX.w * .8, FX.h * .15, 0, FX.w * .8, FX.h * .15, FX.h * .8);
  g2.addColorStop(0, hexA(accent, glowA)); g2.addColorStop(1, "transparent");
  ctx.fillStyle = g2; ctx.fillRect(0, 0, FX.w, FX.h);
}

function hexA(color, a) {
  // color 可能是 hex 或 color-mix 字符串，兜底用固定色
  if (/^#[0-9a-f]{3,8}$/i.test(color.trim())) {
    let c = color.trim().slice(1);
    if (c.length === 3) c = c.split("").map(x => x + x).join("");
    const r = parseInt(c.slice(0, 2), 16), g = parseInt(c.slice(2, 4), 16), b = parseInt(c.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${a})`;
  }
  return `rgba(180,220,190,${a})`;
}

function fxLoop(ts) {
  if (!skinState.particles) { FX.raf = 0; return; }
  const dt = Math.min((ts - FX.last) / 1000 || .016, .05);
  FX.last = ts;
  fxStep(dt);
  FX.raf = requestAnimationFrame(fxLoop);
}

function startFx(systems) {
  if (!FX.canvas) {
    FX.canvas = document.getElementById("fx");
    if (!FX.canvas) return;
    FX.ctx = FX.canvas.getContext("2d");
    window.addEventListener("resize", () => { fxResize(); });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) { cancelAnimationFrame(FX.raf); FX.raf = 0; }
      else if (skinState.particles && !FX.raf) { FX.last = performance.now(); FX.raf = requestAnimationFrame(fxLoop); }
    });
  }
  fxResize();
  FX.systems = systems;
  FX.parts = [];
  if (!skinState.particles) { return; }
  for (const s of systems) {
    const gen = FX_SYSTEMS[s];
    if (gen) gen(Math.round(FX_BASE[s] * densityFactor()));
  }
  cancelAnimationFrame(FX.raf);
  FX.last = performance.now();
  FX.raf = requestAnimationFrame(fxLoop);
}

function restartFxIfActive() {
  if (FX.canvas) startFx(FX.systems.length ? FX.systems : currentSkin().fx);
  else startFx(currentSkin().fx);
}

/* ---------- 情绪微光 ---------- */
window.addEventListener("treehole:scan", (e) => {
  const scan = e.detail || {};
  const veil = document.getElementById("mood-veil");
  document.body.classList.remove("mood-pulse");
  void document.body.offsetWidth; // 重触发动画
  document.body.classList.add("mood-pulse");
  if (!veil) return;
  const v = scan.valence || 0;
  if (v < 0) veil.style.setProperty("--mood-tint", "rgba(235,160,110,.12)");
  else if (v > 0) veil.style.setProperty("--mood-tint", "rgba(150,220,170,.10)");
  else veil.style.setProperty("--mood-tint", "transparent");
  veil.style.opacity = "1";
});

/* ---------- 皮肤抽屉 UI ---------- */
function renderSkinCards() {
  const grid = document.getElementById("skin-grid");
  if (!grid) return;
  grid.innerHTML = "";
  const autoCard = makeSkinCard({ id: "auto", icon: "🌗", name: "跟随时间", desc: "昼夜自动切换" }, skinState.auto);
  grid.appendChild(autoCard);
  for (const s of SKINS) grid.appendChild(makeSkinCard(s, !skinState.auto && skinState.skin === s.id));
}

function makeSkinCard(s, active) {
  const card = document.createElement("div");
  card.className = "skin-card" + (active ? " active" : "");
  const prev = document.createElement("div");
  prev.className = "skin-prev";
  prev.dataset.prev = s.id;
  prev.textContent = s.icon;
  prev.title = s.desc;
  const name = document.createElement("div");
  name.className = "skin-name";
  name.innerHTML = `${s.name}<small>${s.desc}</small>`;
  card.append(prev, name);
  card.onclick = () => {
    if (s.id === "auto") { skinState.auto = true; }
    else { skinState.auto = false; skinState.skin = s.id; }
    applySkin();
  };
  return card;
}

function buildSkinControls() {
  const body = document.getElementById("skin-body");
  if (!body) return;

  const row = (label, ctrl) => {
    const r = document.createElement("div");
    r.className = "ctl-row";
    const l = document.createElement("label"); l.textContent = label;
    r.append(l, ctrl);
    return r;
  };
  const seg = (options, value, onpick) => {
    const s = document.createElement("div"); s.className = "seg";
    for (const [val, txt] of options) {
      const b = document.createElement("button");
      b.textContent = txt;
      b.className = String(val) === String(value) ? "on" : "";
      b.onclick = () => { onpick(val); s.querySelectorAll("button").forEach(x => x.classList.remove("on")); b.classList.add("on"); };
      s.appendChild(b);
    }
    return s;
  };
  const toggle = (value, onpick) => {
    const sw = document.createElement("label"); sw.className = "switch";
    const input = document.createElement("input"); input.type = "checkbox"; input.checked = !!value;
    const i = document.createElement("i");
    input.onchange = () => onpick(input.checked);
    sw.append(input, i);
    return sw;
  };

  const title = (t) => {
    const h = document.createElement("div");
    h.className = "skin-section-title"; h.textContent = t;
    body.appendChild(h);
  };

  title("皮肤");
  const grid = document.createElement("div");
  grid.className = "skin-grid"; grid.id = "skin-grid";
  body.appendChild(grid);

  title("氛围");
  body.appendChild(row("粒子氛围", toggle(skinState.particles, v => {
    skinState.particles = v;
    if (v) restartFxIfActive(); else { cancelAnimationFrame(FX.raf); FX.raf = 0; FX.ctx && FX.ctx.clearRect(0, 0, FX.w, FX.h); }
    saveSkinState();
  })));
  body.appendChild(row("粒子密度", seg([[0, "少"], [1, "中"], [2, "多"]], skinState.density, v => {
    skinState.density = v; restartFxIfActive(); saveSkinState();
  })));
  body.appendChild(row("界面动效（气泡入场/呼吸）", toggle(skinState.anims, v => {
    skinState.anims = v; applySkin();
  })));

  title("自由调节");
  const bright = document.createElement("input");
  bright.type = "range"; bright.min = 10; bright.max = 90; bright.value = skinState.brightness;
  bright.oninput = () => { skinState.brightness = +bright.value; applySkinBrightnessOnly(); };
  body.appendChild(row("明暗", bright));
  body.appendChild(row("字号缩放", seg([[0.9, "小"], [1, "标准"], [1.15, "大"]], skinState.zoom, v => {
    skinState.zoom = v; applySkin();
  })));
  const color = document.createElement("input");
  color.type = "color"; color.className = "color-dot"; color.value = skinState.accent || "#8fd6a0";
  const colorWrap = document.createElement("div");
  colorWrap.style.cssText = "display:flex;gap:8px;align-items:center";
  const resetColor = document.createElement("button");
  resetColor.className = "btn small ghost"; resetColor.textContent = "恢复";
  resetColor.onclick = () => { skinState.accent = ""; color.value = "#8fd6a0"; applySkin(); };
  colorWrap.append(color, resetColor);
  color.oninput = () => { skinState.accent = color.value; applySkin(); };
  body.appendChild(row("强调色", colorWrap));

  title("自定义 CSS（高阶自由）");
  const hint = document.createElement("div");
  hint.className = "privacy-hint";
  hint.style.textAlign = "left";
  hint.textContent = "直接覆盖页面样式，例如：.bubble { border-radius: 4px } ——保存在本机浏览器。";
  body.appendChild(hint);
  const ta = document.createElement("textarea");
  ta.id = "skin-css"; ta.placeholder = "/* 你的 CSS */"; ta.value = skinState.customCSS || "";
  body.appendChild(ta);
  const applyBtn = document.createElement("button");
  applyBtn.className = "btn small"; applyBtn.style.cssText = "margin-top:8px";
  applyBtn.textContent = "应用 CSS";
  applyBtn.onclick = () => { skinState.customCSS = ta.value; applySkin(); };
  body.appendChild(applyBtn);
}

function applySkinBrightnessOnly() {
  const veil = document.getElementById("veil-bright");
  const d = skinState.brightness - 50;
  if (veil) {
    if (d < 0) { veil.style.setProperty("--veil-color", "#000"); veil.style.setProperty("--veil", String(-d / 100 * 0.5)); }
    else { veil.style.setProperty("--veil-color", "#fff"); veil.style.setProperty("--veil", String(d / 100 * 0.18)); }
  }
  saveSkinState();
}

/* ---------- 抽屉开关 ---------- */
function toggleSkinDrawer(open) {
  const d = document.getElementById("skin-drawer");
  if (!d) return;
  d.classList.toggle("open", open);
  const mask = document.getElementById("drawer-mask");
  if (mask) {
    mask.hidden = !(open || document.getElementById("drawer").classList.contains("open") || document.getElementById("history").classList.contains("open"));
  }
}

/* ---------- 初始化 ---------- */
(function initSkins() {
  // 等 DOM 就绪（本脚本置于 body 末尾，直接建 UI）
  buildSkinControls();
  const btn = document.getElementById("btn-skin");
  if (btn) btn.onclick = () => toggleSkinDrawer(true);
  const closeBtn = document.getElementById("btn-close-skin");
  if (closeBtn) closeBtn.onclick = () => toggleSkinDrawer(false);
  const mask = document.getElementById("drawer-mask");
  if (mask) mask.addEventListener("click", () => toggleSkinDrawer(false));

  applySkin();
  // 自动换肤：每 5 分钟重估
  setInterval(() => { if (skinState.auto) applySkin(); }, 5 * 60 * 1000);
})();
