/* vouch operator console: data layer. The API contract is unchanged:
   /api/overview every 4s, /api/sessions/<id> every 5s while inspecting,
   /api/s1/health every 15s, PUT /api/config from the settings modal
   (rails validated server-side), POST /api/runs[...]/stop|restart. */
let state = null, selMode = "shadow", selSession = null, timers = {};

const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const jsq = s => esc(s).replace(/'/g, "&#39;");
const cls = n => n >= 0 ? "pos" : "neg";
const pct = n => (n >= 0 ? "+" : "") + n.toFixed(3) + "%";
function toast(msg) { const t = $("toast"); t.textContent = msg; t.style.display = "block";
  clearTimeout(timers.toast); timers.toast = setTimeout(() => t.style.display = "none", 3500); }

/* live uptime, human shaped; raw seconds stay in the title attribute */
function fmtUptime(s) {
  const t = Math.floor(s);
  if (t < 60) return t + "s";
  const m = Math.floor(t / 60), sec = t % 60;
  if (m < 60) return m + "m " + String(sec).padStart(2, "0") + "s";
  return Math.floor(m / 60) + "h " + String(m % 60).padStart(2, "0") + "m";
}

/* compact number for chart axis labels */
function fmtNum(v) {
  const a = Math.abs(v);
  return a >= 1000 ? v.toFixed(0) : a >= 100 ? v.toFixed(1) : v.toFixed(2);
}

/* colorize one console line: kill-switch red bold, errors red, trade actions emerald */
function hl(line) {
  const e = esc(line);
  if (/kill.?switch|halt/i.test(e)) return `<span class="cout kill">${e}</span>`;
  if (/error|traceback|exception|failed/i.test(e)) return `<span class="cout err">${e}</span>`;
  return e.replace(/\b(BUY|SELL|FILL|FILLED|ENTRY|EXIT)\b/g, '<span class="cout act">$1</span>');
}

/* ---------- console panels ----------
   Every console tail renders as a terminal panel: toolbar (CONSOLE label,
   client-side filter input, live line counter, pause-scroll toggle) over a
   body of line rows (line number, optional leading [HH:MM:SS] timestamp,
   message). HF download-bar noise is dropped client-side. Autoscroll only
   follows the tail when the user is already at the bottom and not paused.
   The filter narrows DISPLAYED lines only, case-insensitively; the
   underlying tail in state is never mutated. */
const consoleState = {}; // key -> {paused, stick, filter, lines}
const NOISE_RE = /Fetching \d+ files|it\/s\]/;
const GLYPH_RE = /^[|_/%.\s=-]+$/; // bare progress-bar artifacts with no words

function parseLine(line) {
  const m = line.match(/^\s*\[(\d{1,2}:\d{2}:\d{2})\]\s*/);
  return m ? [m[1], line.slice(m[0].length)] : ["", line];
}
function consoleRowsHtml(st) {
  const q = (st.filter || "").toLowerCase();
  const shown = st.lines.map((l, i) => [i, l]).filter(([, l]) => !q || l.toLowerCase().includes(q));
  if (!shown.length) return '<span class="clmsg dimtext">' + (q ? "no matching lines" : "no output yet") + '</span>';
  return shown.map(([i, line]) => {
    const [ts, msg] = parseLine(line);
    return `<div class="cline"><span class="clnum">${i + 1}</span><span class="clts">${ts}</span><span class="clmsg">${hl(msg)}</span></div>`;
  }).join("");
}
function consoleCountHtml(st) {
  const q = st.filter || "";
  const total = st.lines.length;
  if (!q) return total + " lines";
  const shown = st.lines.filter(l => l.toLowerCase().includes(q.toLowerCase())).length;
  return shown + " / " + total + " lines";
}
function mountConsole(key, lines) {
  const st = consoleState[key] || (consoleState[key] = { paused: false, stick: true, filter: "" });
  st.lines = (lines || []).filter(l => !NOISE_RE.test(l) && l.trim() && !GLYPH_RE.test(l));
  return `<div class="console">
    <div class="console-bar">
      <span class="console-title">console</span>
      <input class="cfilter" type="text" placeholder="filter lines" aria-label="filter console lines"
             value="${esc(st.filter || "")}"
             oninput="filterConsole('${jsq(key)}', this.value)"
             onkeydown="consoleFilterKey(event, '${jsq(key)}', this)">
      <span class="console-right">
        <span class="console-count">${consoleCountHtml(st)}</span>
        <button type="button" class="cpause ${st.paused ? "on" : ""}" onclick="togglePause('${jsq(key)}', this)">${st.paused ? "paused" : "pause"}</button>
      </span>
    </div>
    <div class="console-body" data-ckey="${esc(key)}">${consoleRowsHtml(st)}</div>
  </div>`;
}
function consoleBodyFor(key) {
  return [...document.querySelectorAll(".console-body[data-ckey]")].find(el => el.dataset.ckey === key);
}
/* filter displayed lines; underlying st.lines is untouched */
function filterConsole(key, value) {
  const st = consoleState[key] || (consoleState[key] = { paused: false, stick: true, filter: "" });
  st.filter = value;
  const body = consoleBodyFor(key);
  if (!body) return;
  body.innerHTML = consoleRowsHtml(st);
  const count = body.closest(".console").querySelector(".console-count");
  if (count) count.textContent = consoleCountHtml(st);
  if (st.stick) body.scrollTop = body.scrollHeight;
}
function consoleFilterKey(e, key, input) {
  if (e.key === "Escape") { input.value = ""; filterConsole(key, ""); }
}
function togglePause(key, btn) {
  const st = consoleState[key] || (consoleState[key] = { paused: false, stick: true, filter: "" });
  st.paused = !st.paused;
  st.stick = !st.paused && st.stick;
  btn.classList.toggle("on", st.paused);
  btn.textContent = st.paused ? "paused" : "pause";
}
function captureConsoleScroll() {
  document.querySelectorAll(".console-body[data-ckey]").forEach(el => {
    const st = consoleState[el.dataset.ckey];
    if (!st) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    st.stick = !st.paused && atBottom;
  });
}
function afterConsoleMount() {
  document.querySelectorAll(".console-body[data-ckey]").forEach(el => {
    const st = consoleState[el.dataset.ckey];
    if (st && st.stick) el.scrollTop = el.scrollHeight;
  });
}

async function api(path, opts) {
  const r = await fetch(path, opts ? {headers: {"Content-Type": "application/json"}, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined} : undefined);
  if (!r.ok) { const e = await r.json().catch(() => ({detail: r.statusText})); throw new Error(e.detail || r.statusText); }
  return r.json();
}

/* Poll-driven renders replace big innerHTML blocks every few seconds. Writing
   identical HTML back replays entrance animations (table rows flash on every
   poll) and drops focus/scroll state, so skip the write when nothing changed. */
const rendered = new Map(); // element -> last html written
function setInner(el, html) {
  if (rendered.get(el) === html) return false;
  rendered.set(el, html);
  el.innerHTML = html;
  return true;
}

/* Session return: newer sessions log return_pct, older sim summaries only
   total_return_pct; read either so legacy sessions report honestly. */
function retOf(sum) {
  return sum ? (sum.return_pct ?? sum.total_return_pct) : undefined;
}

/* ---------- KPI strip (computed only from the overview payload) ---------- */
function kpi(label, value, valCls) {
  return `<div class="kpi"><span class="kpi-label">${label}</span><span class="kpi-value ${valCls || ""}">${value}</span></div>`;
}
function renderKpis() {
  const active = state.active.filter(r => r.running).length;
  const halted = state.sessions.filter(s => s.summary && s.summary.halted).length;
  let best = null;
  for (const s of state.sessions) {
    const r = retOf(s.summary);
    if (typeof r === "number" && (best === null || r > best)) best = r;
  }
  setInner($("kpis"),
    kpi("active runs", active) +
    kpi("sessions found", state.sessions.length) +
    kpi("halted sessions", halted, halted > 0 ? "neg" : "") +
    kpi("best session return", best === null ? "-" : pct(best), best === null ? "" : cls(best)));
}

/* ---------- equity chart (vanilla client-drawn SVG, no libraries) ----------
   Points are only the decisions whose equity is a real number; x is the
   decision index, y the equity. Rendered from 2+ points only; the chart is
   drawn after mount so the line uses true pixel coordinates. */
let eqCharts = [];

function equityChartMount(decisions) {
  if (!decisions || !decisions.length) return "";
  const pts = [];
  decisions.forEach(d => {
    if (typeof d.equity === "number") {
      const label = d.ts ? d.ts.slice(11, 19) : (d.tick !== undefined ? "tick " + d.tick : "");
      pts.push({y: d.equity, label});
    }
  });
  if (pts.length < 2) return '<div class="card"><h2>equity</h2><div class="banner quiet">no data yet for this session</div></div>';
  return `<div class="card"><h2>equity</h2><div class="eqchart" data-eqchart="${eqCharts.push(pts) - 1}"></div></div>`;
}

function eqSvg(pts, w) {
  const H = 170, PL = 52, PR = 14, PT = 12, PB = 10;
  const ys = pts.map(p => p.y);
  let min = Math.min(...ys), max = Math.max(...ys);
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.08;
  min -= pad; max += pad;
  const iw = w - PL - PR, ih = H - PT - PB;
  const X = i => PL + (i / (pts.length - 1)) * iw;
  const Y = v => PT + (1 - (v - min) / (max - min)) * ih;
  const up = pts[pts.length - 1].y >= pts[0].y;
  const col = up ? "var(--pos)" : "var(--neg)";
  const hex = up ? "#45e8a0" : "#ff6b64";
  const line = pts.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(2)} ${Y(p.y).toFixed(2)}`).join(" ");
  const area = `${line} L${X(pts.length - 1).toFixed(2)} ${(PT + ih).toFixed(2)} L${PL} ${(PT + ih).toFixed(2)} Z`;
  const grid = [0, 0.5, 1].map(t => {
    const gy = PT + t * ih, gv = max - t * (max - min);
    return `<line class="eq-grid" x1="${PL}" y1="${gy.toFixed(1)}" x2="${(w - PR).toFixed(1)}" y2="${gy.toFixed(1)}"></line>` +
      `<text class="eq-lab" x="${PL - 8}" y="${(gy + 3).toFixed(1)}">${fmtNum(gv)}</text>`;
  }).join("");
  const last = pts[pts.length - 1];
  const ex = X(pts.length - 1).toFixed(2), ey = Y(last.y).toFixed(2);
  const hits = pts.map((p, i) =>
    `<circle class="eq-hit" cx="${X(i).toFixed(2)}" cy="${Y(p.y).toFixed(2)}" r="10"><title>${esc(p.label)}: ${fmtNum(p.y)}</title></circle>`).join("");
  return `<svg viewBox="0 0 ${w} ${H}" width="${w}" height="${H}" role="img" aria-label="equity over decisions">
    <defs><linearGradient id="eqfill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${hex}" stop-opacity="0.22"></stop>
      <stop offset="1" stop-color="${hex}" stop-opacity="0"></stop>
    </linearGradient></defs>
    ${grid}
    <path d="${area}" fill="url(#eqfill)"></path>
    <path class="eq-line" style="color:${col}" d="${line}" stroke="${col}"></path>
    <circle cx="${ex}" cy="${ey}" r="3.5" fill="${col}"></circle>
    <circle class="eq-pulse" cx="${ex}" cy="${ey}" r="3.5" fill="${col}"></circle>
    ${hits}
  </svg>`;
}

function flushEqCharts() {
  document.querySelectorAll("[data-eqchart]").forEach(el => {
    const pts = eqCharts[+el.dataset.eqchart];
    if (pts) el.innerHTML = eqSvg(pts, Math.max(280, el.clientWidth || 560));
  });
  eqCharts = [];
}

/* ---------- launch panel ---------- */
const MODES = {
  sim:      ["ticks", "epoch", "seed", "cash"],
  shadow:   ["minutes", "interval", "epoch_minutes", "cash"],
  backtest: ["minutes", "epoch_len", "cash"],
  real:     ["minutes", "interval", "epoch_minutes", "max_usd"],
};
const MODE_LABEL = {sim: "Sim", shadow: "Shadow", backtest: "Backtest", real: "Real $"};
const BOOLS = {shadow: [["websocket", "use websocket feed"]], backtest: [["s2", "System-2 rewrites"]]};

function renderModeTabs() {
  $("modetabs").innerHTML = Object.keys(MODES).map(m =>
    `<button class="${m === selMode ? "on" : ""}" onclick="pickMode('${m}')">${MODE_LABEL[m]}</button>`).join("");
  $("modelabel").textContent = MODE_LABEL[selMode];
}
function pickMode(m) { selMode = m; renderModeTabs(); renderParams(); }

function renderParams() {
  const d = state.config.defaults[selMode] || {};
  $("paramfields").innerHTML = MODES[selMode].map(k =>
    `<label>${k.replace(/_/g, " ")}</label><input id="p_${k}" type="number" step="any" value="${d[k] ?? ""}">`).join("")
    + (BOOLS[selMode] || []).map(([k, lbl]) =>
    `<label class="check"><input type="checkbox" id="p_${k}" ${d[k] ?? true ? "checked" : ""} style="width:auto"> ${lbl}</label>`).join("");
}

async function startRun() {
  const params = {};
  MODES[selMode].forEach(k => { const v = $("p_" + k).value; if (v !== "") params[k] = parseFloat(v) || parseInt(v) || v; });
  (BOOLS[selMode] || []).forEach(([k]) => params[k] = $("p_" + k).checked);
  params.venue = $("venue").value;
  if (selMode === "real" && !confirm("This places REAL orders with REAL money. Continue?")) return;
  try {
    const res = await api("/api/runs", {method: "POST", body: {mode: selMode, params, confirm_real: selMode === "real"}});
    toast("started " + res.id); refresh();
  } catch (e) { toast("ERROR: " + e.message); }
}

/* ---------- active runs ---------- */
function renderRuns() {
  const runs = state.active;
  $("runcount").textContent = runs.length ? runs.length + " running" : "";
  captureConsoleScroll();
  if (!runs.length) { setInner($("activeruns"), '<div class="banner">nothing running. Launch something below.</div>'); return; }
  const changed = setInner($("activeruns"), runs.map(r => `
    <div class="card run mode-${esc(r.mode)}">
      <div class="runhead">
        <span class="dot ${r.running ? "on" : (r.exit_code ? "err" : "")}"></span>
        <span class="runid">${esc(r.id)}</span>
        <span class="mode-tag ${esc(r.mode)}">${esc(r.mode)}</span>
        <span class="runstat ${r.running ? "" : (r.exit_code ? "neg" : "dimtext")}"
              title="uptime: ${r.uptime_s}s">${r.running ? fmtUptime(r.uptime_s) : "exit " + r.exit_code}</span>
        <span class="runbtns"><button class="danger" onclick="doStop('${r.id}')">Stop</button>
        <button onclick="doRestart('${r.id}')">Restart</button></span>
      </div>
      ${mountConsole("run:" + r.id, r.console_tail || [])}
    </div>`).join(""));
  if (changed) afterConsoleMount();
}
async function doStop(id) { try { await api(`/api/runs/${id}/stop`, {method: "POST"}); toast("stopped " + id); refresh(); } catch (e) { toast(e.message); } }
async function doRestart(id) { try { await api(`/api/runs/${id}/restart`, {method: "POST"}); toast("restarted " + id); refresh(); } catch (e) { toast(e.message); } }

/* ---------- sessions + inspector ---------- */
function renderSessions() {
  $("sesscount").textContent = state.sessions.length ? state.sessions.length + " found" : "";
  setInner($("sessions"), state.sessions.map(s => {
    const sum = s.summary;
    const ret = retOf(sum);
    const hasRet = typeof ret === "number";
    const sub = sum ? `equity ${sum.final_equity ?? "-"} · ${sum.closed_trades ?? 0} trades` : "no summary";
    return `<div class="sess ${s.id === selSession ? "sel" : ""}" onclick="inspect('${s.id}')">
      <div class="sess-l">
        <div class="sess-t">
          <span class="dot ${sum && sum.halted ? "err" : ""}"></span>
          <span class="name">${esc(s.id)}</span>
          ${sum && sum.halted ? '<span class="mode-tag halted">halt</span>' : ""}
        </div>
        <div class="sess-b">${esc(sub)}</div>
      </div>
      <span class="retpill ${hasRet ? cls(ret) : "dimtext"}">${hasRet ? pct(ret) : "-"}</span>
    </div>`;
  }).join("") || '<div class="dimtext">no sessions yet</div>');
}

async function inspect(id) {
  selSession = id;
  renderSessions();
  clearInterval(timers.insp);
  await loadSession();
  timers.insp = setInterval(loadSession, 5000);
}

async function loadSession() {
  if (!selSession) return;
  let s;
  try { s = await api(`/api/sessions/${selSession}?limit=40`); } catch { return; }
  $("inspname").textContent = "(" + s.id + ")";
  eqCharts = []; // mounts below push their points; dropped when the render is skipped
  let html = "";
  if (s.summary && s.summary.halted) {
    html += `<div class="killbanner">kill-switch: ${esc(s.summary.halt_reason || "halted")}</div>`;
  }
  if (s.summary) {
    const m = s.summary;
    const ret = retOf(m);
    html += `<div class="card"><div class="tiles">
      <div class="tile"><span class="tile-label">return</span><span class="tile-value ${cls(ret || 0)}">${pct(ret || 0)}</span></div>
      <div class="tile"><span class="tile-label">trades</span><span class="tile-value">${m.closed_trades ?? "-"}</span></div>
      <div class="tile"><span class="tile-label">equity</span><span class="tile-value">${m.final_equity ?? "-"}</span></div>
      <div class="tile"><span class="tile-label">halted</span><span class="tile-value ${m.halted ? "neg" : "pos"}">${m.halted ? "YES: " + esc(m.halt_reason || "") : "no"}</span></div>
    </div></div>`;
  }
  html += equityChartMount(s.decisions);
  if (s.decisions.length) {
    html += `<div class="card"><h2>Decisions (last ${s.decisions.length})</h2><table><tr>
      <th>time</th><th>price</th><th>mode</th><th>action</th><th>conv</th><th>P(flat)</th><th>veto / acted</th><th>equity</th></tr>` +
    s.decisions.slice().reverse().map(d => {
      const dec = d.decision || {};
      const pf = dec.probs ? dec.probs.flat : null;
      const act = dec.final_action || "";
      const actCls = /buy|long/i.test(act) ? "pos" : (/sell|exit|short/i.test(act) ? "negsoft" : "");
      const cv = dec.conviction;
      const cvCell = typeof cv === "number"
        ? `<span class="convwrap"><span class="convbar"><i style="width:${(Math.max(0, Math.min(1, cv)) * 100).toFixed(1)}%"></i></span><span>${cv}</span></span>`
        : `${cv ?? ""}`;
      return `<tr><td class="dimtext">${(d.ts || "").slice(11, 19)}</td><td>${d.price ?? ""}</td>
        <td>${dec.mode || "entry"}</td><td class="${actCls}">${esc(act)}</td>
        <td>${cvCell}</td><td>${pf != null ? pf.toFixed(3) : ""}</td>
        <td class="dimtext">${esc(d.acted || dec.veto || "")}</td><td>${d.equity ?? ""}</td></tr>`;
    }).join("") + "</table></div>";
  }
  if (s.trades.length) {
    const pnls = s.trades.map(t => (typeof t.pnl_usd === "number" ? Math.abs(t.pnl_usd) : 0));
    const maxAbs = Math.max(1e-9, ...pnls);
    html += `<div class="card"><h2>Trades</h2><table><tr><th>tick</th><th>action</th><th>price</th><th>pnl</th><th>reason</th></tr>` +
      s.trades.slice().reverse().map(t => {
        const p = typeof t.pnl_usd === "number" ? t.pnl_usd : null;
        const bar = p === null ? "" :
          `<span class="pnlbar"><i class="${cls(p)}" style="${p >= 0
            ? `left:50%;width:${((p / maxAbs) * 50).toFixed(2)}%`
            : `right:50%;width:${((-p / maxAbs) * 50).toFixed(2)}%`}"></i></span>`;
        const cell = `<span class="pnlwrap">${bar}<span class="${p === null ? "" : cls(p)}">${t.pnl_usd ?? ""}</span></span>`;
        return `<tr><td>${t.tick ?? ""}</td><td class="pos">${esc(t.action || "")}</td><td>${t.price ?? ""}</td>
        <td>${cell}</td><td>${esc(t.reason || "")}</td></tr>`;
      }).join("") + "</table></div>";
  }
  if (s.s2.length) {
    html += `<div class="card"><h2>System-2 rewrites</h2><table><tr><th>time</th><th>backend</th><th>applied</th><th>rejected</th></tr>` +
      s.s2.slice().reverse().map(r => {
        const ap = r.s2.applied;
        const chips = ap && typeof ap === "object" && !Array.isArray(ap)
          ? Object.entries(ap).map(([k, v]) => `<span class="chip chip-pos">${esc(k)} ${esc(String(v))}</span>`).join(" ")
          : (ap ? `<span class="chip chip-pos">${esc(String(ap))}</span>` : '<span class="dimtext">none</span>');
        const rej = r.s2.rejected ? (r.s2.rejected.why || JSON.stringify(r.s2.rejected)) : "";
        return `<tr><td class="dimtext">${(r.ts || r.tick || "").toString().slice(0, 19)}</td>
        <td>${esc((r.s2.backend || "").slice(0, 40))}</td><td>${chips}</td>
        <td class="rej">${esc(rej)}</td></tr>`;
      }).join("") + "</table></div>";
  }
  if (s.console && s.console.length) html += `<div class="card"><h2>Console</h2>${mountConsole("insp:" + selSession, s.console)}</div>`;
  captureConsoleScroll();
  if (setInner($("inspector"), html || '<div class="banner">no data yet for this session</div>')) {
    flushEqCharts();
    afterConsoleMount();
  }
}

/* ---------- settings ---------- */
function openSettings() {
  const cfg = state.config;
  $("cfgerr").style.display = "none";
  $("rulefields").innerHTML = Object.keys(cfg.rules).map(k => {
    const rail = state.rails[k];
    const railtxt = rail ? ` (${rail[0]} .. ${rail[1]})` : "";
    const isBool = typeof cfg.rules[k] === "boolean" || Array.isArray(cfg.rules[k]);
    return `<label>${k}${railtxt}</label><input id="r_${k}" value="${isBool ? esc(JSON.stringify(cfg.rules[k])) : esc(String(cfg.rules[k]))}">`;
  }).join("");
  $("cfg_fee").value = cfg.fee_bps; $("cfg_product").value = cfg.product;
  $("r_s1_provider").value = cfg.s1.provider;
  $("r_s1_url").value = cfg.s1.url; $("r_s1_model").value = cfg.s1.model;
  $("r_s1_api_key").value = cfg.s1.api_key;
  $("r_s2_base_url").value = cfg.s2.base_url; $("r_s2_model").value = cfg.s2.model;
  $("r_s2_api_key").value = cfg.s2.api_key;
  renderS1Hint();
  $("defaultfields").innerHTML = Object.entries(cfg.defaults).flatMap(([mode, d]) =>
    Object.entries(d).map(([k, v]) =>
      `<label>${mode}.${k.replace(/_/g, " ")}</label><input id="d_${mode}_${k}" type="${typeof v === "number" ? "number" : "text"}" step="any" value="${v}">`)).join("");
  $("settingsbg").classList.add("open");
}
function renderS1Hint() {
  const hints = {
    local: "Loads the laya checkpoint in-process. Slowest cold start (~15s), no network needed.",
    server: "Uses a running laya server (laya.serve) over /v1/systemone. A cold server can take a while on its first predict - that is normal, later calls are fast.",
    openrouter: "Sends the typed questions to an OpenAI-compatible endpoint via OpenRouter. Needs the api key + model id. Reply JSON is parsed into the same answer shape, so rails/logging/analysis all work unchanged.",
  };
  $("s1hint").textContent = hints[$("r_s1_provider").value] || "";
}
async function probeS1() {
  $("s1health").textContent = "checking...";
  try {
    // save current s1 fields first so the probe reflects what you typed
    const s1 = {provider: $("r_s1_provider").value, url: $("r_s1_url").value,
                model: $("r_s1_model").value, api_key: $("r_s1_api_key").value};
    await api("/api/config", {method: "PUT", body: {config: {s1}}});
    const h = await api("/api/s1/health");
    $("s1health").innerHTML = h.probe && h.probe.status === "ok"
      ? `<b class="pos">up</b> adapters: ${esc(JSON.stringify(h.probe.loaded))}`
      : `<b class="neg">${esc(JSON.stringify(h.probe))}</b>`;
  } catch (e) { $("s1health").innerHTML = `<b class="neg">${esc(e.message)}</b>`; }
}
function closeSettings() { $("settingsbg").classList.remove("open"); }
async function saveSettings() {
  const cfg = {rules: {}, defaults: {}, s1: {}, s2: {}};
  Object.keys(state.config.rules).forEach(k => {
    let v = $("r_" + k).value;
    try { v = JSON.parse(v); } catch {}
    if (typeof v === "string" && !isNaN(parseFloat(v)) && k !== "product") v = parseFloat(v);
    cfg.rules[k] = v;
  });
  Object.entries(state.config.defaults).forEach(([mode, d]) => { cfg.defaults[mode] = {};
    Object.keys(d).forEach(k => { let v = $("d_" + mode + "_" + k).value;
      if (v === "true" || v === "on") v = true; if (v === "false" || v === "") v = false;
      cfg.defaults[mode][k] = isNaN(parseFloat(v)) || typeof d[k] === "string" ? v : parseFloat(v); }); });
  cfg.fee_bps = parseFloat($("cfg_fee").value); cfg.product = $("cfg_product").value;
  cfg.s1 = {provider: $("r_s1_provider").value, url: $("r_s1_url").value.trim(),
            model: $("r_s1_model").value.trim(), api_key: $("r_s1_api_key").value};
  cfg.s2 = {base_url: $("r_s2_base_url").value.trim(), model: $("r_s2_model").value.trim(),
            api_key: $("r_s2_api_key").value};
  try { await api("/api/config", {method: "PUT", body: {config: cfg}}); toast("saved"); $("cfgerr").style.display = "none"; closeSettings(); refresh(); }
  catch (e) {
    const err = $("cfgerr");
    err.textContent = "rails rejected the save: " + e.message;
    err.style.display = "block";
    document.querySelectorAll("#settingsbg input").forEach(i => i.classList.remove("err"));
    Object.keys(state.config.rules).forEach(k => {
      if (e.message.includes(k)) { const el = $("r_" + k); if (el) el.classList.add("err"); }
    });
    const bad = document.querySelector("#settingsbg input.err");
    if (bad) bad.scrollIntoView({block: "center", behavior: "smooth"});
    toast("ERROR: " + e.message);
  }
}

/* ---------- main loop ---------- */
function renderVenues() {
  // venue picker is a segmented control; the hidden #venue input keeps the
  // original form contract (startRun reads $("venue").value)
  if (!$("venue").value || !state.venues.some(v => v.id === $("venue").value)) {
    $("venue").value = state.config.venue || (state.venues[0] ? state.venues[0].id : "");
  }
  $("venueseg").innerHTML = state.venues.map(v =>
    `<button type="button" class="${v.id === $("venue").value ? "on" : ""}" onclick="pickVenue('${v.id}')">${esc(v.label)}${v.configured || !v.live ? "" : " (keys missing)"}</button>`).join("");
  const v = state.venues.find(x => x.id === $("venue").value);
  if (v) $("venuedocs").textContent = v.docs + (v.live && !v.configured ? " MISSING: " + v.missing_env.join(", ") : "");
  $("realbanner").innerHTML = state.real_mode_allowed ? "" :
    `<div class="banner warn">Real-money runs are blocked from this UI by default (by design). Start run_real.py by hand,
     or set VOUCH_UI_ALLOW_REAL=1 in the server environment if you know what that means.</div>`;
}
function pickVenue(id) { $("venue").value = id; renderVenues(); }

async function refresh() {
  try { state = await api("/api/overview"); } catch { return; }
  $("clock").textContent = "updated " + new Date().toLocaleTimeString();
  $("headfee").innerHTML =
    `<span class="hchip">fee ${esc(String(state.config.fee_bps))} bps/side</span>` +
    `<span class="hchip">venue ${esc(String(state.config.venue))}</span>`;
  renderKpis();
  renderVenues(); renderParams(); renderRuns(); renderSessions();
  if ($("settingsbg").classList.contains("open")) { /* leave settings alone while open */ }
  if (selSession && !$("inspname").textContent) loadSession();
}

/* System-1 server health dot: pulsing emerald only when the configured
   provider is a reachable server; red when unreachable; neutral when n/a. */
async function checkS1() {
  const d = $("s1dot");
  try {
    const h = await api("/api/s1/health");
    if (h.probe && h.probe.status === "ok") d.className = "dot on pulse";
    else if (typeof h.probe === "string" && h.probe.indexOf("n/a") === 0) d.className = "dot";
    else d.className = "dot err";
  } catch { d.className = "dot err"; }
}

renderModeTabs();
refresh();
checkS1();
setInterval(refresh, 4000);
setInterval(() => { if (selSession) loadSession(); }, 5000);
setInterval(checkS1, 15000);
