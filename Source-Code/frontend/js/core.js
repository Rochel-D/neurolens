// Shared state, API client, safe rendering helpers, icons, modals, charts, speech.
export const state = {
  token: sessionStorage.getItem("nl_token"), user: null, config: null,
  audience: "patient", lang: localStorage.getItem("nl_lang") || "en",
  thread: null, attach: { report: null, mri: null }, busy: false,
};
const listeners = {};
export const on = (ev, fn) => (listeners[ev] = listeners[ev] || []).push(fn);
export const emit = (ev, data) => (listeners[ev] || []).forEach((fn) => fn(data));

export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? esc(u) : "#");
export const fmtDate = (d) => { if (!d) return ""; const x = new Date(d.length === 10 ? d + "T00:00:00" : d); return isNaN(x) ? d : x.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }); };
export const num = (v) => (v == null ? "" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 3 }));

export function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 3400); }

export async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  const r = await fetch(path, { method: opts.method || (opts.body ? "POST" : "GET"), headers, body: opts.body });
  if (opts.raw) { if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Request failed (${r.status})`); return r; }
  let data = null; try { data = await r.json(); } catch { /* empty */ }
  if (r.status === 401 && state.token) { emit("logout"); throw new Error("Please sign in again"); }
  if (!r.ok) throw new Error((data && data.detail) || `Request failed (${r.status})`);
  return data;
}

export async function download(path, fallbackName) {
  const r = await api(path, { raw: true });
  const blob = await r.blob();
  const cd = r.headers.get("Content-Disposition") || "";
  const name = (cd.match(/filename="([^"]+)"/) || [])[1] || fallbackName;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

export async function makePdf({ report_id = null, mri_id = null, button = null }) {
  const old = button ? button.innerHTML : "";
  if (button) { button.disabled = true; button.innerHTML = `<span class="spinner"></span> Preparing PDF…`; }
  try {
    const e = await api("/api/exports", { json: { report_id, mri_id, language: state.lang } });
    await download(`/api/exports/${e.token}`, e.filename);
    toast(`Downloaded ${e.filename}`);
  } catch (err) { toast(err.message); }
  finally { if (button) { button.disabled = false; button.innerHTML = old; } }
}

/* Safe markdown subset: escape first, then bold/italic/links/lists/headings/tables. */
export function md(src) {
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])[_*]([^_*\n]+?)[_*](?=[\s.,)!?]|$)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (m, t, u) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`);
  const out = []; let list = null; let table = null;
  const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const flushList = () => { if (list) { out.push(`<${list.tag}>${list.items.join("")}</${list.tag}>`); list = null; } };
  const flushTable = () => {
    if (!table) return;
    const [head, ...body] = table;
    out.push(`<div class="tbl"><table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${body.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
    table = null;
  };
  src = String(src || "").replace(/【(\d+)[^】]*】/g, "[$1]");
  for (const raw of src.split("\n")) {
    const line = raw.trimEnd();
    if (/^\s*\|.*\|\s*$/.test(line)) { flushList(); if (!/^\s*\|[\s:|-]+\|\s*$/.test(line)) (table = table || []).push(cells(line)); continue; }
    flushTable();
    const li = line.match(/^\s*([-*•]|\d+\.)\s+(.*)/);
    if (li) {
      const tag = /\d/.test(li[1]) ? "ol" : "ul";
      if (list && list.tag !== tag) flushList();
      list = list || { tag, items: [] };
      list.items.push(tag === "ol" ? `<li value="${parseInt(li[1], 10)}">${inline(li[2])}</li>` : `<li>${inline(li[2])}</li>`); // keep "3." as 3 even after bullets
      continue;
    }
    flushList();
    const h = line.match(/^#{1,4}\s+(.*)/);
    if (h) out.push(`<h4>${inline(h[1])}</h4>`);
    else if (line.trim() === "---") out.push("<hr>");
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  flushList(); flushTable();
  return out.join("");
}
export const plain = (s) => String(s || "").replace(/【\d+[^】]*】|\[\d+\]/g, "").replace(/[*_#|>`]/g, "").replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").replace(/-{3,}/g, "").replace(/\n{2,}/g, "\n");

/* Icons (stroke SVG). */
const P = {
  home: "M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  chat: "M4 5h16v11H8l-4 4z",
  lab: "M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3",
  brain: "M12 4c-4 0-7 3-7 7 0 2 1 4 2 5v3h10v-3c1-1 2-3 2-5 0-4-3-7-7-7zM12 4v15M8 9c1 1 3 1 4 0M12 13c1 1 3 1 4 0",
  memory: "M12 3a9 9 0 1 0 9 9M12 7v5l3 3M21 3v6h-6",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
  out: "M15 4h4v16h-4M10 8l-4 4 4 4M6 12h11",
  sun: "M12 4V2M12 22v-2M4 12H2M22 12h-2M5.6 5.6 4.2 4.2M19.8 19.8l-1.4-1.4M5.6 18.4l-1.4 1.4M19.8 4.2l-1.4 1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
  moon: "M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z",
  clip: "M21 12l-8.5 8.5a5 5 0 0 1-7-7L14 5a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L15 8",
  mic: "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3",
  send: "M4 12l16-8-6 16-3-7z",
  speak: "M4 9v6h4l5 4V5L8 9zM16 9a4 4 0 0 1 0 6M19 6a8 8 0 0 1 0 12",
  stop: "M7 7h10v10H7z",
  copy: "M9 9h11v11H9zM5 15H4V4h11v1",
  up: "M7 10v10H4V10zM7 10l4-7a2 2 0 0 1 3 2l-1 5h6a2 2 0 0 1 2 2l-2 7a2 2 0 0 1-2 1H7",
  down: "M7 14V4H4v10zM7 14l4 7a2 2 0 0 0 3-2l-1-5h6a2 2 0 0 0 2-2l-2-7a2 2 0 0 0-2-1H7",
  pdf: "M6 2h9l5 5v15H6zM14 2v6h6M9 13h6M9 17h6",
  dl: "M12 4v12M7 11l5 5 5-5M5 20h14",
  x: "M6 6l12 12M18 6 6 18",
  trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13",
  upload: "M12 16V4M7 9l5-5 5 5M5 20h14",
  camera: "M4 8h3l2-3h6l2 3h3v11H4zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  chart: "M4 20V4M4 20h16M8 16l4-5 3 3 5-7",
  globe: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18",
  edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
  plus: "M12 5v14M5 12h14",
  refresh: "M20 11a8 8 0 1 0-2 6M20 4v7h-7",
  check: "M5 12l5 5 9-10",
  key: "M15 7a4 4 0 1 1-3.5 6L4 20H2v-2l7-7.5A4 4 0 0 1 15 7z",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
};
export const icon = (n, cls = "") => `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="${P[n] || ""}"/></svg>`;

/* Modals instead of native confirm()/prompt(). */
export function modal({ title, body = "", ok = "OK", cancel = "Cancel", danger = false, wide = false, onOpen }) {
  return new Promise((resolve) => {
    const bg = document.createElement("div");
    bg.className = "modal-bg";
    bg.innerHTML = `<div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true"><h3>${esc(title)}</h3><div class="mbody">${body}</div>
      <div class="row" style="justify-content:flex-end">${cancel ? `<button class="btn" data-r="0">${esc(cancel)}</button>` : ""}<button class="btn ${danger ? "danger" : "primary"}" data-r="1">${esc(ok)}</button></div></div>`;
    document.body.appendChild(bg);
    const close = (v) => { bg.remove(); resolve(v); };
    bg.addEventListener("click", (e) => { if (e.target === bg) close(false); });
    $$("[data-r]", bg).forEach((b) => b.addEventListener("click", async () => {
      if (b.dataset.r === "1" && onOpen && onOpen.submit) { const v = await onOpen.submit(bg); if (v === false) return; close(v); } else close(b.dataset.r === "1");
    }));
    if (onOpen && onOpen.init) onOpen.init(bg);
    $("[data-r='1']", bg).focus();
  });
}
export const confirmBox = (title, text, ok = "Continue", danger = false) => modal({ title, body: `<p class="muted" style="margin:0">${esc(text)}</p>`, ok, danger });

/* Charts (SVG, no library). */
export function sparkline(points, { w = 240, h = 70, big = false } = {}) {
  if (!points.length) return "";
  const vals = points.map((p) => p.value);
  const last = points[points.length - 1];
  const lows = points.map((p) => p.ref_low).filter((x) => x != null), highs = points.map((p) => p.ref_high).filter((x) => x != null);
  let lo = Math.min(...vals, ...(lows.length ? [Math.min(...lows)] : [])), hi = Math.max(...vals, ...(highs.length ? [Math.max(...highs)] : []));
  if (lo === hi) { lo -= 1; hi += 1; }
  const pad = (hi - lo) * 0.15; lo -= pad; hi += pad;
  const L = big ? 46 : 6, R = big ? 48 : 6, T = big ? 22 : 8, B = big ? 30 : 8;
  const X = (i) => L + (points.length === 1 ? (w - L - R) / 2 : (i * (w - L - R)) / (points.length - 1));
  const Y = (v) => T + (1 - (v - lo) / (hi - lo)) * (h - T - B);
  let band = "";
  if (last.ref_low != null || last.ref_high != null) {
    const a = last.ref_high != null ? Y(last.ref_high) : T, b = last.ref_low != null ? Y(last.ref_low) : h - B;
    band = `<rect x="${L}" y="${Math.min(a, b)}" width="${w - L - R}" height="${Math.abs(b - a)}" fill="var(--ok-bg)"/>`;
  }
  const path = points.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)} ${Y(p.value).toFixed(1)}`).join("");
  const col = { normal: "var(--ok)", low: "var(--low)", high: "var(--high)", critical_low: "var(--crit)", critical_high: "var(--crit)" };
  const dots = points.map((p, i) => `<circle cx="${X(i)}" cy="${Y(p.value)}" r="${big ? 5 : 3.5}" fill="${col[p.status] || "var(--accent)"}" stroke="var(--panel)" stroke-width="2"><title>${esc(fmtDate(p.date))}: ${esc(num(p.value))}</title></circle>`).join("");
  let axis = "";
  if (big) {
    axis = points.map((p, i) => `<text x="${X(i)}" y="${h - 8}" text-anchor="middle" font-size="11" fill="var(--ink3)">${esc(fmtDate(p.date))}</text>`).join("")
      + points.map((p, i) => `<text x="${X(i)}" y="${Y(p.value) - 10}" text-anchor="middle" font-size="12" font-weight="600" fill="var(--ink)">${esc(num(p.value))}</text>`).join("");
    if (last.ref_low != null) axis += `<text x="${L - 6}" y="${Y(last.ref_low) + 4}" text-anchor="end" font-size="10" fill="var(--ink3)">${esc(num(last.ref_low))}</text>`;
    if (last.ref_high != null) axis += `<text x="${L - 6}" y="${Y(last.ref_high) + 4}" text-anchor="end" font-size="10" fill="var(--ink3)">${esc(num(last.ref_high))}</text>`;
  }
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="trend">${band}<path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2"/>${dots}${axis}</svg>`;
}

/* Range bar for one lab value. */
export function rangeBar(r) {
  const lo = r.ref_low, hi = r.ref_high, v = r.value;
  if (lo == null && hi == null) return `<div class="tiny muted">No range</div>`;
  const a = lo ?? 0, b = hi ?? a * 2 + 1, span = b - a || 1;
  const min = Math.min(a - span * 0.5, v), max = Math.max(b + span * 0.5, v);
  const pct = (x) => ((x - min) / (max - min)) * 100;
  return `<div class="rng"><div class="band" style="left:${pct(a)}%;width:${pct(b) - pct(a)}%"></div><div class="dot d-${esc(r.status)}" style="left:${pct(v)}%"></div></div>
    <div class="rng-l"><span>${lo != null ? esc(num(lo)) : ""}</span><span>${r.range_source === "report" ? "range from report" : "standard range"}</span><span>${hi != null ? esc(num(hi)) : ""}</span></div>`;
}
export const STATUS_TXT = { normal: "Normal", low: "Low", high: "High", critical_low: "Critical low", critical_high: "Critical high" };

/* Speech: read aloud + voice input (browser built-in, no keys). */
const SPEECH_LANG = { en: "en-IN", hi: "hi-IN", kok: "en-IN" };
export function speak(text, btn) {
  if (!("speechSynthesis" in window)) { toast("Read aloud is not supported in this browser"); return; }
  if (speechSynthesis.speaking) { speechSynthesis.cancel(); $$(".speaking").forEach((b) => { b.classList.remove("on", "speaking"); b.innerHTML = icon("speak"); }); if (btn && btn._was) { btn._was = false; return; } }
  const u = new SpeechSynthesisUtterance(plain(text).slice(0, 4000));
  const isHindi = /[ऀ-ॿ]/.test(text);
  u.lang = isHindi ? "hi-IN" : SPEECH_LANG[state.lang] || "en-IN";
  const v = speechSynthesis.getVoices().find((x) => x.lang.replace("_", "-").startsWith(u.lang.slice(0, 2)) && /IN/.test(x.lang)) || speechSynthesis.getVoices().find((x) => x.lang.startsWith(u.lang.slice(0, 2)));
  if (v) u.voice = v;
  u.rate = 0.95;
  if (btn) { btn.classList.add("on", "speaking"); btn.innerHTML = icon("stop"); btn._was = true; u.onend = () => { btn.classList.remove("on", "speaking"); btn.innerHTML = icon("speak"); btn._was = false; }; }
  speechSynthesis.speak(u);
}
export const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
export function listen(onText, btn) {
  if (!SR) { toast("Voice input works in Chrome, Edge and Safari"); return null; }
  const rec = new SR();
  rec.lang = SPEECH_LANG[state.lang] || "en-IN"; rec.interimResults = true; rec.continuous = false;
  rec.onresult = (e) => onText([...e.results].map((r) => r[0].transcript).join(" "), e.results[e.results.length - 1].isFinal);
  rec.onerror = (e) => toast(e.error === "not-allowed" ? "Microphone permission was blocked" : `Voice input: ${e.error}`);
  rec.onend = () => btn && btn.classList.remove("rec");
  rec.start(); if (btn) btn.classList.add("rec");
  return rec;
}

export function wireDrop(zone, input, handler) {
  input.addEventListener("change", () => { if (input.files[0]) handler(input.files[0]); input.value = ""; });
  ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("over"); }));
  zone.addEventListener("drop", (e) => e.dataTransfer.files[0] && handler(e.dataTransfer.files[0]));
}
export async function upload(path, file) { const fd = new FormData(); fd.append("file", file); return api(path, { body: fd }); }
export const go = (hash) => { if (location.hash !== hash) location.hash = hash; else emit("route"); };
