/* 树洞挚友 · 皮肤与氛围系统 v2
   8 皮肤（含自定义媒体背景）+ 粒子引擎 + 环境音合成 + 高自由度调节
   + 随时间自动换肤 + 情绪微光 + 配置导入导出 */
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
  { id: "custom", icon: "🖼️", name: "自定义", desc: "上传图片/视频当背景", fx: ["fireflies"] },
];

const PARTICLE_CHOICES = [
  ["auto", "跟随皮肤"], ["none", "无"],
  ["fireflies", "✨萤火"], ["bubbles", "🫧气泡"], ["petals", "🌸樱花"],
  ["stars", "⭐星星"], ["embers", "🔥余烬"], ["motes", "💫光尘"], ["mist", "🌫️薄雾"], ["leaves", "🍂落叶"],
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
  particles: true, density: 1, particleChoice: "auto",
  anims: true, brightness: 50, zoom: 1,
  accent: "", customCSS: "",
  media: null,               // {type:'image'|'video', name} blob 存 IndexedDB
  mediaDim: 0.55, mediaBlur: 2,
  ambient: { type: "off", vol: 0.5 },
};
let skinState = (() => {
  try {
    const saved = JSON.parse(localStorage.getItem("treehole_skin") || "{}");
    return { ...DEFAULT_SKIN_STATE, ...saved, ambient: { ...DEFAULT_SKIN_STATE.ambient, ...(saved.ambient || {}) } };
  } catch { return { ...DEFAULT_SKIN_STATE }; }
})();
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
if (reducedMotion) { skinState.particles = false; skinState.anims = false; }

function saveSkinState() { localStorage.setItem("treehole_skin", JSON.stringify(skinState)); }
function currentSkinId() {
  if (skinState.auto) return autoSkinByHour(new Date().getHours());
  if (skinState.skin === "custom") return "custom";
  return skinState.skin;
}
function currentSkin() { return SKINS.find(s => s.id === currentSkinId()) || SKINS[0]; }

/* ============================================================
   IndexedDB：自定义媒体持久化
   ============================================================ */
function idb() {
  return new Promise((res, rej) => {
    const r = indexedDB.open("treehole-skin", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("media");
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
}
async function idbPut(value) {
  const db = await idb();
  return new Promise((res, rej) => {
    const tx = db.transaction("media", "readwrite");
    tx.objectStore("media").put(value, "bg");
    tx.oncomplete = () => res();
    tx.onerror = () => rej(tx.error);
  });
}
async function idbGet() {
  const db = await idb();
  return new Promise((res, rej) => {
    const tx = db.transaction("media", "readonly");
    const rq = tx.objectStore("media").get("bg");
    rq.onsuccess = () => res(rq.result || null);
    rq.onerror = () => rej(rq.error);
  });
}
async function idbDel() {
  const db = await idb();
  return new Promise((res, rej) => {
    const tx = db.transaction("media", "readwrite");
    tx.objectStore("media").delete("bg");
    tx.oncomplete = () => res();
    tx.onerror = () => rej(tx.error);
  });
}

/* ---------- 媒体背景 ---------- */
let mediaURL = null;

async function setCustomMedia(file) {
  if (!file) return;
  const type = file.type.startsWith("video") ? "video" : file.type.startsWith("image") ? "image" : null;
  if (!type) { alert("只支持图片或视频文件"); return; }
  if (file.size > 120 * 1024 * 1024) { alert("文件太大（超过 120MB），换个小一点的吧"); return; }
  await idbPut({ blob: file, type, name: file.name, ts: Date.now() });
  skinState.media = { type, name: file.name };
  skinState.auto = false;
  skinState.skin = "custom";
  applySkin();
  renderMediaStatus();
}

async function clearCustomMedia() {
  await idbDel();
  skinState.media = null;
  if (skinState.skin === "custom") skinState.skin = "forest";
  applySkin();
  renderMediaStatus();
}

async function applyCustomMedia() {
  const holder = document.getElementById("media-bg");
  const dim = document.getElementById("media-dim");
  if (!holder || !dim) return;
  if (mediaURL) { URL.revokeObjectURL(mediaURL); mediaURL = null; }
  holder.innerHTML = "";
  const active = currentSkinId() === "custom" && skinState.media;
  holder.hidden = !active;
  dim.hidden = !active;
  if (!active) return;
  const rec = await idbGet().catch(() => null);
  if (!rec || !rec.blob) { skinState.media = null; holder.hidden = true; dim.hidden = true; return; }
  mediaURL = URL.createObjectURL(rec.blob);
  let el;
  if (rec.type === "video") {
    el = document.createElement("video");
    el.src = mediaURL; el.autoplay = true; el.loop = true;
    el.muted = true; el.playsInline = true;
    el.onerror = () => { el.remove(); holder.hidden = true; dim.hidden = true; };
  } else {
    el = document.createElement("img");
    el.src = mediaURL; el.alt = "";
    el.onerror = () => { el.remove(); holder.hidden = true; dim.hidden = true; };
  }
  holder.appendChild(el);
  applyMediaFilters();
}

function applyMediaFilters() {
  const dim = document.getElementById("media-dim");
  const media = document.querySelector("#media-bg img, #media-bg video");
  if (dim) dim.style.background = `rgba(5, 9, 7, ${skinState.mediaDim})`;
  if (media) media.style.filter = skinState.mediaBlur > 0 ? `blur(${skinState.mediaBlur}px)` : "";
}

/* ---------- 应用皮肤 ---------- */
function applySkin() {
  const id = currentSkinId();
  document.documentElement.setAttribute("data-skin", id);

  document.body.classList.toggle("anims-on", skinState.anims);

  const veil = document.getElementById("veil-bright");
  const d = skinState.brightness - 50;
  if (veil) {
    if (d < 0) { veil.style.setProperty("--veil-color", "#000"); veil.style.setProperty("--veil", String(-d / 100 * 0.5)); }
    else { veil.style.setProperty("--veil-color", "#fff"); veil.style.setProperty("--veil", String(d / 100 * 0.18)); }
  }

  document.documentElement.style.setProperty("--zoom", String(skinState.zoom));

  document.documentElement.style.removeProperty("--accent");
  document.documentElement.style.removeProperty("--accent-dim");
  if (skinState.accent) {
    document.documentElement.style.setProperty("--accent", skinState.accent);
    document.documentElement.style.setProperty("--accent-dim", `color-mix(in srgb, ${skinState.accent} 55%, #000)`);
  }

  let tag = document.getElementById("custom-css");
  if (!tag) { tag = document.createElement("style"); tag.id = "custom-css"; document.head.appendChild(tag); }
  tag.textContent = skinState.customCSS || "";

  applyCustomMedia(); // 异步，不阻塞
  startFx(effectiveFx());
  renderSkinCards();
  saveSkinState();
}

function effectiveFx() {
  if (!skinState.particles) return [];
  const c = skinState.particleChoice;
  if (c === "auto") return currentSkin().fx;
  if (c === "none") return [];
  return [c];
}

/* ============================================================
   粒子引擎（单 canvas，多系统叠加）
   ============================================================ */
const FX = { canvas: null, ctx: null, parts: [], systems: [], raf: 0, last: 0, w: 0, h: 0, shootTimer: 0 };

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
      ph: R(0, Math.PI * 2), sp: R(.2, .6), drift: R(-.15, .15),
    });
  },
  bubbles(n) {
    for (let i = 0; i < n; i++) FX.parts.push({
      t: "bu", x: R(0, FX.w), y: R(0, FX.h), r: R(2, 7), vy: R(.25, .8), ph: R(0, Math.PI * 2),
    });
  },
  petals(n) {
    for (let i = 0; i < n; i++) FX.parts.push({
      t: "pe", x: R(0, FX.w), y: R(-FX.h, FX.h), s: R(4, 8), vy: R(.4, 1.1),
      rot: R(0, Math.PI * 2), vr: R(-.02, .02), ph: R(0, Math.PI * 2), hue: Math.random() > .5,
    });
  },
  stars(n) {
    for (let i = 0; i < n; i++) FX.parts.push({
      t: "st", x: R(0, FX.w), y: R(0, FX.h * .85), r: R(.5, 1.6), ph: R(0, Math.PI * 2), sp: R(.5, 1.8),
    });
  },
  embers(n) {
    for (let i = 0; i < n; i++) FX.parts.push({
      t: "em", x: R(0, FX.w), y: R(0, FX.h), r: R(.8, 2.2), vy: R(.3, .9), ph: R(0, Math.PI * 2), drift: R(-.2, .2),
    });
  },
  motes(n) {
    for (let i = 0; i < n; i++) FX.parts.push({
      t: "mo", x: R(0, FX.w), y: R(0, FX.h), r: R(.6, 1.4), vx: R(-.12, .12), vy: R(-.08, .08), a: R(.06, .2),
    });
  },
  leaves(n) {
    for (let i = 0; i < n; i++) FX.parts.push({
      t: "le", x: R(0, FX.w), y: R(-FX.h, FX.h), s: R(5, 10), vy: R(.25, .7), rot: R(0, Math.PI * 2), vr: R(-.015, .015), ph: R(0, Math.PI * 2),
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
        p.ph += .16 * p.sp * dt; p.x += p.drift * dt * 60; p.y += Math.sin(p.ph) * .3;
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
        ctx.strokeStyle = "rgba(180,225,245,.28)"; ctx.lineWidth = 1;
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

  if (skin.fx.includes("stars") && skinState.particleChoice === "auto") {
    FX.shootTimer -= dt;
    if (FX.shootTimer <= 0) {
      FX.shootTimer = R(5, 13);
      FX.parts.push({ t: "shoot", x: R(FX.w * .3, FX.w), y: R(0, FX.h * .3), vx: -7.5, vy: 3.4, life: 1.1, max: 1.1 });
    }
  }
  FX.parts = FX.parts.filter(p => p.t !== "shoot" || p.life > 0);

  const glowA = .035 + .02 * Math.sin(performance.now() / 2600);
  const g2 = ctx.createRadialGradient(FX.w * .8, FX.h * .15, 0, FX.w * .8, FX.h * .15, FX.h * .8);
  g2.addColorStop(0, hexA(accent, glowA)); g2.addColorStop(1, "transparent");
  ctx.fillStyle = g2; ctx.fillRect(0, 0, FX.w, FX.h);
}

function hexA(color, a) {
  if (/^#[0-9a-f]{3,8}$/i.test((color || "").trim())) {
    let c = color.trim().slice(1);
    if (c.length === 3) c = c.split("").map(x => x + x).join("");
    const r = parseInt(c.slice(0, 2), 16), g = parseInt(c.slice(2, 4), 16), b = parseInt(c.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${a})`;
  }
  return `rgba(180,220,190,${a})`;
}

function fxLoop(ts) {
  if (!skinState.particles && !FX.parts.length) { FX.raf = 0; return; }
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
      else if (!FX.raf) { FX.last = performance.now(); FX.raf = requestAnimationFrame(fxLoop); }
    });
  }
  fxResize();
  FX.systems = systems;
  FX.parts = [];
  for (const s of systems) {
    const gen = FX_SYSTEMS[s];
    if (gen) gen(Math.round(FX_BASE[s] * densityFactor()));
  }
  cancelAnimationFrame(FX.raf);
  if (skinState.particles || FX.parts.length) {
    FX.last = performance.now();
    FX.raf = requestAnimationFrame(fxLoop);
  } else { FX.ctx && FX.ctx.clearRect(0, 0, FX.w, FX.h); }
}

function restartFxIfActive() {
  startFx(effectiveFx());
}

/* ============================================================
   环境音（WebAudio 实时合成，无需音频文件）
   ============================================================ */
const Ambience = {
  ctx: null, master: null, nodes: [], crackleTimer: 0,
  ensure() {
    if (this.ctx) return;
    this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    this.master = this.ctx.createGain();
    this.master.gain.value = skinState.ambient.vol;
    this.master.connect(this.ctx.destination);
  },
  noiseBuffer(seconds = 2) {
    const ctx = this.ctx;
    const buf = ctx.createBuffer(1, ctx.sampleRate * seconds, ctx.sampleRate);
    const d = buf.getChannelData(0);
    let lastOut = 0;
    for (let i = 0; i < d.length; i++) {
      const white = Math.random() * 2 - 1;
      d[i] = (lastOut + .02 * white) / 1.02;  // 棕噪声更柔和
      lastOut = d[i];
      d[i] *= 3.2;
    }
    return buf;
  },
  start(type) {
    this.stop();
    if (type === "off") return;
    this.ensure();
    this.ctx.resume();
    const ctx = this.ctx;
    const src = ctx.createBufferSource();
    src.buffer = this.noiseBuffer(); src.loop = true;
    const filter = ctx.createBiquadFilter();
    const gain = ctx.createGain();
    if (type === "rain") {         // 雨声：偏亮白噪 + 低通
      filter.type = "lowpass"; filter.frequency.value = 1400; filter.Q.value = .4;
      gain.gain.value = .5;
    } else if (type === "stream") { // 溪流：带通 + 缓慢起伏
      filter.type = "bandpass"; filter.frequency.value = 700; filter.Q.value = .7;
      gain.gain.value = .65;
      const lfo = ctx.createOscillator(); lfo.frequency.value = .18;
      const lfoGain = ctx.createGain(); lfoGain.gain.value = 260;
      lfo.connect(lfoGain); lfoGain.connect(filter.frequency); lfo.start();
      this.nodes.push(lfo);
    } else {                        // 篝火：低沉底噪 + 随机噼啪
      filter.type = "lowpass"; filter.frequency.value = 320;
      gain.gain.value = .8;
      this.scheduleCrackle();
    }
    src.connect(filter); filter.connect(gain); gain.connect(this.master);
    src.start();
    this.nodes.push(src, filter, gain);
  },
  scheduleCrackle() {
    const tick = () => {
      if (!this.ctx || skinState.ambient.type !== "fire") return;
      const ctx = this.ctx;
      const burst = ctx.createBufferSource();
      burst.buffer = this.noiseBuffer(.05);
      const hp = ctx.createBiquadFilter(); hp.type = "highpass"; hp.frequency.value = 1800;
      const g = ctx.createGain();
      const t = ctx.currentTime;
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(.25 + Math.random() * .5, t + .006);
      g.gain.exponentialRampToValueAtTime(.001, t + .05 + Math.random() * .08);
      burst.connect(hp); hp.connect(g); g.connect(this.master);
      burst.start();
      this.crackleTimer = setTimeout(tick, 120 + Math.random() * 650);
    };
    this.crackleTimer = setTimeout(tick, 200);
  },
  setVolume(v) { if (this.master) this.master.gain.value = v; },
  stop() {
    clearTimeout(this.crackleTimer);
    for (const n of this.nodes) { try { n.stop ? n.stop() : n.disconnect(); } catch {} }
    this.nodes = [];
  },
};

function setAmbience(type) {
  skinState.ambient.type = type;
  Ambience.start(type === "off" ? null : type);
  saveSkinState();
}

/* ---------- 情绪微光 ---------- */
window.addEventListener("treehole:scan", (e) => {
  const scan = e.detail || {};
  const veil = document.getElementById("mood-veil");
  document.body.classList.remove("mood-pulse");
  void document.body.offsetWidth;
  document.body.classList.add("mood-pulse");
  if (!veil) return;
  const v = scan.valence || 0;
  if (v < 0) veil.style.setProperty("--mood-tint", "rgba(235,160,110,.12)");
  else if (v > 0) veil.style.setProperty("--mood-tint", "rgba(150,220,170,.10)");
  else veil.style.setProperty("--mood-tint", "transparent");
  veil.style.opacity = "1";
});

/* ============================================================
   皮肤抽屉 UI
   ============================================================ */
function renderSkinCards() {
  const grid = document.getElementById("skin-grid");
  if (!grid) return;
  grid.innerHTML = "";
  const autoCard = makeSkinCard({ id: "auto", icon: "🌗", name: "跟随时间", desc: "昼夜自动切换" }, skinState.auto);
  grid.appendChild(autoCard);
  for (const s of SKINS) {
    const active = !skinState.auto && skinState.skin === s.id;
    const card = makeSkinCard(s, active);
    if (s.id === "custom") card.title = "先在下方上传图片/视频";
    grid.appendChild(card);
  }
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
    if (s.id === "auto") { skinState.auto = true; applySkin(); return; }
    if (s.id === "custom" && !skinState.media) {
      alert("先在下方「我的专属背景」上传一张图片或一段视频吧");
      return;
    }
    skinState.auto = false; skinState.skin = s.id; applySkin();
  };
  return card;
}

function renderMediaStatus() {
  const elx = document.getElementById("media-status");
  if (!elx) return;
  if (skinState.media) {
    const icon = skinState.media.type === "video" ? "🎬" : "🖼️";
    elx.textContent = `${icon} ${skinState.media.name}（${skinState.media.type === "video" ? "视频" : "图片"}·已保存，重启后仍在）`;
  } else {
    elx.textContent = "还没有上传背景";
  }
}

function buildSkinControls() {
  const body = document.getElementById("skin-body");
  if (!body) return;

  const title = (t) => {
    const h = document.createElement("div");
    h.className = "skin-section-title"; h.textContent = t;
    body.appendChild(h);
  };
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
      b.onclick = () => {
        onpick(val);
        s.querySelectorAll("button").forEach(x => x.classList.remove("on"));
        b.classList.add("on");
      };
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

  /* --- 皮肤 --- */
  title("皮肤");
  const grid = document.createElement("div");
  grid.className = "skin-grid"; grid.id = "skin-grid";
  body.appendChild(grid);

  /* --- 我的专属背景 --- */
  title("🖼️ 我的专属背景（图片 / 视频）");
  const btns = document.createElement("div");
  btns.className = "media-btns";
  const imgBtn = document.createElement("button"); imgBtn.className = "btn small"; imgBtn.textContent = "选图片";
  const vidBtn = document.createElement("button"); vidBtn.className = "btn small"; vidBtn.textContent = "选视频";
  const clearBtn = document.createElement("button"); clearBtn.className = "btn small"; clearBtn.textContent = "清除";
  const imgInput = document.createElement("input"); imgInput.type = "file"; imgInput.accept = "image/*"; imgInput.hidden = true;
  const vidInput = document.createElement("input"); vidInput.type = "file"; vidInput.accept = "video/*"; vidInput.hidden = true;
  imgBtn.onclick = () => imgInput.click();
  vidBtn.onclick = () => vidInput.click();
  clearBtn.onclick = () => clearCustomMedia();
  imgInput.onchange = () => { if (imgInput.files[0]) setCustomMedia(imgInput.files[0]); imgInput.value = ""; };
  vidInput.onchange = () => { if (vidInput.files[0]) setCustomMedia(vidInput.files[0]); vidInput.value = ""; };
  btns.append(imgBtn, vidBtn, clearBtn, imgInput, vidInput);
  body.appendChild(btns);
  const status = document.createElement("div");
  status.className = "media-status"; status.id = "media-status";
  body.appendChild(status);

  const dimSlider = document.createElement("input");
  dimSlider.type = "range"; dimSlider.min = 0; dimSlider.max = 90; dimSlider.value = skinState.mediaDim * 100;
  dimSlider.oninput = () => { skinState.mediaDim = dimSlider.value / 100; applyMediaFilters(); saveSkinState(); };
  body.appendChild(row("背景压暗", dimSlider));
  const blurSlider = document.createElement("input");
  blurSlider.type = "range"; blurSlider.min = 0; blurSlider.max = 14; blurSlider.value = skinState.mediaBlur;
  blurSlider.oninput = () => { skinState.mediaBlur = +blurSlider.value; applyMediaFilters(); saveSkinState(); };
  body.appendChild(row("背景模糊", blurSlider));

  /* --- 氛围 --- */
  title("氛围");
  body.appendChild(row("粒子氛围", toggle(skinState.particles, v => {
    skinState.particles = v; restartFxIfActive(); saveSkinState();
  })));
  body.appendChild(row("粒子密度", seg([[0, "少"], [1, "中"], [2, "多"]], skinState.density, v => {
    skinState.density = v; restartFxIfActive(); saveSkinState();
  })));
  // 粒子类型自由混搭
  const chipsRow = document.createElement("div");
  chipsRow.className = "chips";
  for (const [val, txt] of PARTICLE_CHOICES) {
    const c = document.createElement("span");
    c.className = "chip pick" + (String(skinState.particleChoice) === String(val) ? " on" : "");
    c.textContent = txt;
    c.onclick = () => {
      skinState.particleChoice = val;
      if (val !== "auto" && val !== "none") skinState.particles = true;
      restartFxIfActive(); saveSkinState();
      chipsRow.querySelectorAll(".chip").forEach(x => x.classList.remove("on"));
      c.classList.add("on");
    };
    chipsRow.appendChild(c);
  }
  body.appendChild(row("粒子类型", chipsRow));
  body.appendChild(row("界面动效", toggle(skinState.anims, v => {
    skinState.anims = v; applySkin();
  })));

  /* --- 环境音 --- */
  title("🔊 环境音（实时合成）");
  const vol = document.createElement("input");
  vol.type = "range"; vol.min = 0; vol.max = 100; vol.value = skinState.ambient.vol * 100;
  vol.oninput = () => { skinState.ambient.vol = vol.value / 100; Ambience.setVolume(skinState.ambient.vol); saveSkinState(); };
  body.appendChild(row("音量", vol));
  const ambSeg = seg([["off", "关闭"], ["rain", "🌧️ 雨声"], ["stream", "🏞️ 溪流"], ["fire", "🔥 篝火"]],
    skinState.ambient.type, v => setAmbience(v));
  body.appendChild(row("声音", ambSeg));
  const ambHint = document.createElement("div");
  ambHint.className = "privacy-hint"; ambHint.style.textAlign = "left";
  ambHint.textContent = "由 WebAudio 现场合成，不需要任何音频文件；浏览器要求点击一次才会出声。";
  body.appendChild(ambHint);

  /* --- 自由调节 --- */
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
  resetColor.className = "btn small"; resetColor.textContent = "恢复";
  resetColor.onclick = () => { skinState.accent = ""; color.value = "#8fd6a0"; applySkin(); };
  colorWrap.append(color, resetColor);
  color.oninput = () => { skinState.accent = color.value; applySkin(); };
  body.appendChild(row("强调色", colorWrap));

  /* --- 自定义 CSS --- */
  title("自定义 CSS（高阶自由）");
  const hint = document.createElement("div");
  hint.className = "privacy-hint"; hint.style.textAlign = "left";
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

  /* --- 配置分享 --- */
  title("📦 皮肤配置");
  const shareRow = document.createElement("div");
  shareRow.className = "media-btns";
  const expBtn = document.createElement("button"); expBtn.className = "btn small"; expBtn.textContent = "导出配置";
  const impBtn = document.createElement("button"); impBtn.className = "btn small"; impBtn.textContent = "导入配置";
  const impInput = document.createElement("input"); impInput.type = "file"; impInput.accept = ".json,application/json"; impInput.hidden = true;
  expBtn.onclick = () => {
    const { media, ...cfg } = skinState; // 媒体文件（可能上百MB）不进配置
    const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "treehole-skin.json";
    a.click();
    URL.revokeObjectURL(a.href);
  };
  impBtn.onclick = () => impInput.click();
  impInput.onchange = async () => {
    const f = impInput.files[0];
    if (!f) return;
    try {
      const data = JSON.parse(await f.text());
      skinState = { ...DEFAULT_SKIN_STATE, ...data, media: skinState.media, ambient: { ...DEFAULT_SKIN_STATE.ambient, ...(data.ambient || {}) } };
      applySkin();
      renderMediaStatus();
      alert("皮肤配置已导入 ✓");
      buildDrawerFresh(); // 重建控件以刷新选中态
    } catch { alert("配置文件解析失败"); }
    impInput.value = "";
  };
  shareRow.append(expBtn, impBtn, impInput);
  body.appendChild(shareRow);
  renderMediaStatus();
}

function buildDrawerFresh() {
  const body = document.getElementById("skin-body");
  if (body) { body.innerHTML = ""; buildSkinControls(); renderSkinCards(); }
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
    const other = document.getElementById("drawer").classList.contains("open") ||
                  document.getElementById("history").classList.contains("open");
    mask.hidden = !(open || other);
  }
}

/* ---------- 初始化 ---------- */
(function initSkins() {
  buildSkinControls();
  const btn = document.getElementById("btn-skin");
  if (btn) btn.onclick = () => toggleSkinDrawer(true);
  const closeBtn = document.getElementById("btn-close-skin");
  if (closeBtn) closeBtn.onclick = () => toggleSkinDrawer(false);
  const mask = document.getElementById("drawer-mask");
  if (mask) mask.addEventListener("click", () => toggleSkinDrawer(false));

  applySkin();
  setInterval(() => { if (skinState.auto) applySkin(); }, 5 * 60 * 1000);
})();
