// QuoteGate console: polls the local server (quote_gate/console.py) and renders its snapshots.
"use strict";

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => Array.from(root.querySelectorAll(s));
const NS = "http://www.w3.org/2000/svg";
const fmt = new Intl.NumberFormat("en-US");
const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);
const n = (v) => (v === null || v === undefined ? "—" : fmt.format(v));
const signed = (v) => (v === null || v === undefined ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") + fmt.format(Math.abs(v)));
const us = (ns) => (ns === undefined || ns === null ? "—" : ns >= 1e6 ? (ns / 1e6).toFixed(2) + " ms" : (ns / 1e3).toFixed(1) + " µs");
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

const STATUS = {
  fresh: { tone: "good", label: "Fresh" },
  stale: { tone: "warn", label: "Stale" },
  disconnected: { tone: "bad", label: "Disconnected" },
  invalid: { tone: "bad", label: "Invalid" },
  no_quote: { tone: "neutral", label: "No quote" },
};
const DECISIONS = [
  ["order", "Order sent"], ["at_target", "At target"], ["no_signal", "No signal"],
  ["outstanding", "Order in flight"], ["halted_unknown", "Halted (unknown)"], ["cooldown", "Cooldown"],
];
const REASONS = { stale: "stale", disconnected: "disconnected", invalid: "invalid", no_quote: "no quote",
  insufficient_quantity: "size too small", not_allowed: "not allowed" };

const state = { tab: "live", snap: null, busy: false, history: { sent: [], filled: [] }, seenEvents: new Map(),
  guide: null, faultStates: {}, sawRun: false, scorecardShown: false, fills: { broker: null, bot: null },
  lastLife: {}, filter: "all", quantLoaded: false, evidenceLoaded: false };

// ---------- helpers ----------
async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

let toastTimer;
function toast(msg, tone = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.dataset.tone = tone;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3800);
}

function svg(tag, attrs = {}, text) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
}

function el(tag, attrs = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") e.textContent = v;
    else if (k === "class") e.className = v;
    else e.setAttribute(k, v);
  }
  for (const c of [].concat(children)) if (c) e.append(c);
  return e;
}

// Animate a number toward its new value (integers only; text set directly when reduced motion).
function tween(node, value, format = n) {
  if (!node) return;
  const from = node._v ?? 0;
  node._v = value;
  if (value === null || value === undefined || typeof value !== "number" || reduceMotion || from === value) {
    node.textContent = format(value);
    return;
  }
  const start = performance.now(), dur = 300;
  cancelAnimationFrame(node._raf);
  const step = (t) => {
    const k = Math.min(1, (t - start) / dur), e = 1 - Math.pow(1 - k, 3);
    node.textContent = format(Math.round(from + (value - from) * e));
    if (k < 1) node._raf = requestAnimationFrame(step);
  };
  node._raf = requestAnimationFrame(step);
}

// Live counts and states are shown exactly as received, never animated through intermediate values.
function setNum(node, value) {
  if (!node) return;
  cancelAnimationFrame(node._raf);
  node._v = value;
  node.textContent = n(value);
}

function setTone(node, tone, label) {
  if (!node) return;
  const changed = node.dataset.tone !== tone;
  node.dataset.tone = tone;
  if (label !== undefined) $(".label", node).textContent = label;
  if (changed && !reduceMotion) {
    node.classList.remove("pulse");
    void node.offsetWidth;
    node.classList.add("pulse");
  }
}

function reveal(container) {
  container.classList.remove("reveal");
  void container.offsetWidth;
  container.classList.add("reveal");
  $$(".card", container).forEach((c, i) => { c.style.animationDelay = `${i * 40}ms`; });
}

// ---------- tabs ----------
function showTab(name, focus = false) {
  state.tab = name;
  $$(".rail-item").forEach((b) => (b.dataset.tab === name ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current")));
  $$(".tab").forEach((t) => { t.hidden = t.id !== `tab-${name}`; });
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  if (name === "quant" && !state.quantLoaded) loadQuant();
  if (name === "evidence" && !state.evidenceLoaded) loadEvidence();
  if (focus) $("#main").focus();
  if (state.snap) render(state.snap);
}

// ---------- run controls ----------
async function toggleRun(e, guided = false) {
  e?.preventDefault();
  const running = state.snap && ["running", "starting", "settling"].includes(state.snap.state);
  try {
    if (running) {
      await api("/api/stop", {});
      toast("Stopping: letting in-flight orders resolve…");
    } else {
      const seed = parseInt($("#seed").value, 10);
      const seconds = guided ? 90 : parseInt($("#seconds").value, 10);
      if (!Number.isInteger(seed) || seed < 1 || seed > 1000000) return toast("Seed must be an integer from 1 to 1,000,000.", "bad");
      await api("/api/run", { seed, seconds });
      state.history = { sent: [], filled: [] };
      state.seenEvents.clear();
      state.lastLife = {};
      state.faultStates = {};
      state.scorecardShown = false;
      state.guide = guided ? { ids: {} } : null;
      toast(guided ? "Guided demo started: follow the three steps." : `Run started: seed ${seed}, ${seconds} s.`);
      showTab("live");
      requestAnimationFrame(() => reveal($("#live-body")));
    }
    poll(true);
  } catch (err) {
    toast(err.message, "bad");
  }
}

const FAULT_TARGET = { pause_feed: "venue", pause_feed_1s: "venue", resume_feed: "venue", drop_connection: "venue",
  drop_next_ack: "orders", disconnect_after_exec: "orders", restart_mid_order: "orders" };
function flash(node) {
  if (!node || reduceMotion) return;
  node.classList.remove("flash");
  void node.offsetWidth;
  node.classList.add("flash");
}

async function fault(kind, venue = "A") {
  try {
    const r = await api("/api/fault", { kind, venue });
    toast(`Venue ${venue}: ${r.note}. Expect: ${r.expect}.`);
    if (FAULT_TARGET[kind] === "venue") flash($(`.venue[data-venue="${venue}"]`));
    else { flash($(".span-faults")); flash($('[data-k="unresolved"]')?.closest(".card")); }
    poll(true);
    return r;
  } catch (err) {
    toast(err.message, "bad");
    return null;
  }
}

// ---------- polling ----------
let pollTimer, renderFrame = 0;
async function poll() {
  clearTimeout(pollTimer);
  if (state.busy) { state.pollAgain = true; return; }   // one request at a time; a forced poll runs right after
  state.busy = true;
  try {
    const detail = state.tab === "orders" ? "?detail=1" : "";
    state.snap = await api(`/api/live${detail}`);
    if (!renderFrame) renderFrame = requestAnimationFrame(() => { renderFrame = 0; render(state.snap); });
  } catch (err) {
    setTone($("#trading-badge"), "bad", "Console offline");
  } finally {
    state.busy = false;
    if (state.pollAgain) { state.pollAgain = false; poll(); } else schedule();
  }
}
function schedule() {
  clearTimeout(pollTimer);
  const s = state.snap?.state;
  const active = s === "running" || s === "starting" || s === "settling";
  pollTimer = setTimeout(poll, active ? (state.tab === "orders" ? 500 : 250) : 1500);
}

// ---------- render: shared ----------
function render(s) {
  const idle = s.state === "idle";
  const active = ["running", "starting", "settling"].includes(s.state);
  const btn = $("#run-btn");
  btn.classList.toggle("btn-primary", !active);
  btn.classList.toggle("btn-stop", active);
  $("span", btn).textContent = active ? (s.state === "settling" ? "Settling…" : "Stop run") : "Start run";
  btn.disabled = s.state === "settling";
  if (btn._active !== active) btn._active = active, $("svg", btn).innerHTML = active ? '<rect x="3.5" y="3.5" width="9" height="9" rx="1.5" fill="currentColor"/>' : '<path d="M4 2.5v11l9-5.5z" fill="currentColor"/>';
  $("#seed").disabled = active;
  $("#seconds").disabled = active;
  $$("[data-fault], [data-venue-act]").forEach((b) => {
    b.disabled = s.state !== "running";
    b.title = s.state === "running" ? "" : "Start a run to inject faults";
  });
  $("#fault-hint").hidden = s.state === "running";
  if (active) state.sawRun = true;
  if (s.faults) announceFaults(s.faults);
  if (s.state === "complete" && state.sawRun && !state.scorecardShown && s.reconciliation) {
    state.scorecardShown = true;
    showScorecard(s);
  }

  const tb = $("#trading-badge");
  if (idle) setTone(tb, "neutral", "Idle");
  else if (s.error) setTone(tb, "bad", "Run error");
  else {
    const t = { trading: ["good", "Trading"], no_signal: ["warn", "No signal · reference not fresh"],
      halted_unknown: ["unknown", "Halted · unknown order"], halted_lost_record: ["bad", "Halted · lost record"] }[s.trading] || ["neutral", s.trading];
    if (s.state === "complete") setTone(tb, s.trading === "halted_lost_record" ? "bad" : "neutral", s.trading === "halted_lost_record" ? "Complete · lost record" : "Run complete");
    else if (s.state === "settling") setTone(tb, "accent", "Settling");
    else setTone(tb, t[0], t[1]);
  }
  $("#run-clock").textContent = idle ? "--" : `${(s.elapsed_s ?? 0).toFixed(1)} s / ${s.seconds} s · seed ${s.seed}`;
  if (s.error) toast(`Run error: ${s.error}`, "bad");

  $("#live-empty").hidden = !idle;
  $("#live-body").hidden = idle;
  $("#orders-empty").hidden = !idle;
  $("#orders-body").hidden = idle;
  if (idle) return;
  if (state.tab === "live") { renderStatus(s); renderGuide(s); renderFaultResults(s); renderLive(s); }
  if (state.tab === "orders" && s.reconciliation) renderOrders(s);
}

// ---------- render: live ----------
function renderLive(s) {
  const c = s.counters;
  const rejected = Object.values(c.rejected).reduce((a, b) => a + b, 0);
  setNum($('[data-k="sent"]'), c.sent);
  setNum($('[data-k="filled"]'), c.filled);
  setNum($('[data-k="rejected"]'), rejected);
  $('[data-k="rejected-why"]').textContent = rejected ? Object.entries(c.rejected).map(([k, v]) => `${REASONS[k] || k.replace(/_/g, " ")} ${n(v)}`).join(" · ") : "none";
  const stuck = s.unresolved.filter(([, st]) => st !== "SENT");
  const inflight = s.unresolved.length - stuck.length;
  setNum($('[data-k="unresolved"]'), stuck.length);
  $('[data-k="unresolved-why"]').textContent = (stuck.length ? stuck.map(([o, st]) => `${o} ${st.replace("_", " ")}`).join(", ") : "none UNKNOWN or lost")
    + (inflight ? ` · ${inflight} in flight` : "");
  for (const k of ["sent", "filled"]) {
    const h = state.history[k];
    if (s.state === "running" || !h.length) { h.push(c[k]); if (h.length > 120) h.shift(); }
    spark($(`[data-spark="${k}"]`), h);
  }
  for (const vid of ["A", "B"]) renderVenue(vid, s.venues[vid], s.stale_ms, s.state === "running");
  renderTimeline(s.timeline, s.elapsed_s);
  renderStrategy(s);
  renderRoute(s.route_preview);
  setNum($('[data-k="timeouts"]'), c.timeouts + c.unknown_disconnect);
  setNum($('[data-k="by-status"]'), c.resolved_by_status + c.resolved_not_received);
  setNum($('[data-k="lost"]'), c.lost_record);
  renderEvents(s);
}

function spark(node, data) {
  if (!node.childElementCount) node.append(svg("path", { class: "area" }), svg("path", { class: "line" }));
  const [area, line] = node.children;
  if (data.length < 2) { area.removeAttribute("d"); line.removeAttribute("d"); return; }
  const max = Math.max(...data), min = Math.min(...data), span = max - min || 1;
  const pts = data.map((v, i) => [(i / (data.length - 1)) * 120, 26 - ((v - min) / span) * 24]);
  const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
  area.setAttribute("d", `${d}L120,28L0,28Z`);
  line.setAttribute("d", d);
}

function venueTemplate(vid) {
  return `
    <div class="venue-h">
      <div><div class="venue-name">Venue ${vid}</div><div class="venue-role"></div></div>
      <span class="badge" data-tone="neutral"><span class="dot"></span><span class="label">—</span></span>
    </div>
    <div class="venue-mid">
      <svg class="ring" viewBox="0 0 64 64" aria-hidden="true"><circle class="bg" cx="32" cy="32" r="26"/><circle class="fg" cx="32" cy="32" r="26"/><text x="32" y="32"></text></svg>
      <div class="age"><div class="label">Last quote age</div><div><span class="mono" data-f="age">—</span> <small>ms</small></div><small data-f="freshness"></small></div>
    </div>
    <div class="quote">
      <div class="side bid"><div class="row"><span class="label">Bid</span><span class="label">Size</span></div>
        <div class="row"><span class="px mono" data-f="bid">—</span><span class="mono" data-f="bid_qty">—</span></div><div class="sizebar"><i data-f="bid_bar"></i></div></div>
      <div class="side ask"><div class="row"><span class="label">Ask</span><span class="label">Size</span></div>
        <div class="row"><span class="px mono" data-f="ask">—</span><span class="mono" data-f="ask_qty">—</span></div><div class="sizebar"><i data-f="ask_bar"></i></div></div>
    </div>
    <dl class="vstats">
      <div><dt>Sequence</dt><dd class="mono" data-f="sequence"></dd></div>
      <div><dt>Epoch</dt><dd class="mono" data-f="epoch"></dd></div>
      <div><dt>Connects</dt><dd class="mono" data-f="connects"></dd></div>
      <div><dt>Accepted</dt><dd class="mono" data-f="accepted"></dd></div>
      <div><dt>Rejected</dt><dd class="mono" data-f="rejected"></dd></div>
      <div><dt>Sequence gaps</dt><dd class="mono" data-f="gaps"></dd></div>
    </dl>
    <div class="venue-actions">
      <button class="btn btn-sm" data-venue-act="pause"></button>
      <button class="btn btn-sm" data-venue-act="drop">Drop connection</button>
    </div>`;
}

function renderVenue(vid, v, staleMs, running) {
  const root = $(`.venue[data-venue="${vid}"]`);
  if (!root.dataset.ready) {
    root.innerHTML = venueTemplate(vid);
    root.dataset.ready = "1";
    $('[data-venue-act="pause"]', root).addEventListener("click", () => fault(root._paused ? "resume_feed" : "pause_feed", vid));
    $('[data-venue-act="drop"]', root).addEventListener("click", () => fault("drop_connection", vid));
  }
  const st = STATUS[v.status] || STATUS.no_quote;
  root.dataset.tone = st.tone;
  $(".venue-role", root).textContent = v.role === "market data only" ? "Market data only" : "Reference + execution";
  setTone($(".badge", root), st.tone, st.label);
  const live = v.status !== "disconnected" && v.age_ms !== null;
  $('[data-f="age"]', root).textContent = live ? (v.age_ms >= 1000 ? (v.age_ms / 1000).toFixed(1) + "k" : v.age_ms.toFixed(1)) : "—";
  $('[data-f="freshness"]', root).textContent = v.paused ? "feed paused at the venue" : v.status === "stale" ? `over the ${staleMs} ms threshold` : "";
  const ring = $(".ring", root), C = 2 * Math.PI * 26;
  const frac = live ? Math.max(0, 1 - v.age_ms / staleMs) : 0;
  ring.style.setProperty("--tone", `var(--${{ good: "good", warn: "warn", bad: "bad", neutral: "neutral" }[st.tone]})`);
  const fg = $(".fg", ring);
  fg.setAttribute("stroke-dasharray", C.toFixed(2));
  fg.setAttribute("stroke-dashoffset", (C * (1 - frac)).toFixed(2));
  $("text", ring).textContent = live ? `${Math.round(frac * 100)}%` : "—";
  for (const f of ["bid", "ask", "bid_qty", "ask_qty", "sequence", "epoch", "connects", "accepted", "rejected", "gaps"]) {
    $(`[data-f="${f}"]`, root).textContent = n(v[f]);
  }
  $('[data-f="bid_bar"]', root).style.width = `${Math.min(100, v.bid_qty ?? 0)}%`;   // synthetic sizes are 1–100
  $('[data-f="ask_bar"]', root).style.width = `${Math.min(100, v.ask_qty ?? 0)}%`;
  root._paused = v.paused;
  const pb = $('[data-venue-act="pause"]', root);
  pb.textContent = v.paused ? "Resume feed" : "Pause feed";
  pb.disabled = !running;
  $('[data-venue-act="drop"]', root).disabled = !running;
}

// Segments are placed at their run time (elapsed + relative time) and redrawn from each snapshot; only
// the view's horizontal offset is eased between snapshots (presentation only, never the statuses).
function renderTimeline(tl, elapsed) {
  const root = $("#timeline");
  const W = tl.window_s, u = 1000 / W;
  let summary = [];
  for (const vid of Object.keys(tl.transitions)) {
    let row = $(`[data-tl="${vid}"]`, root);
    if (!row) {
      const bar = svg("svg", { viewBox: "0 0 1000 20", preserveAspectRatio: "none", class: "tl-bar" });
      bar.append(svg("g", { class: "tl-track" }));
      row = el("div", { class: "tl-row", "data-tl": vid }, [el("span", { class: "tl-label mono", text: vid }), bar]);
      root.append(row);
    }
    const track = $(".tl-track", row);
    const segs = [];
    let cur = tl.start[vid], t0 = -W;
    for (const [t, to] of tl.transitions[vid]) { segs.push([t0, t, cur]); cur = to; t0 = t; }
    segs.push([t0, 0, cur]);
    track.replaceChildren(...segs.filter(([a, b]) => b > a).map(([a, b, st]) => {
      const tone = st ? (STATUS[st] || STATUS.no_quote).tone : null;
      return svg("rect", { x: ((elapsed + a) * u).toFixed(2), y: 2, width: Math.max(1, (b - a) * u).toFixed(2), height: 16, rx: 2,
        fill: tone ? `var(--${tone})` : "var(--surface-3)", opacity: tone === "good" ? 0.75 : 0.9 });
    }));
    if (track._t === undefined || elapsed < track._t || elapsed - track._t > 2) {   // new run or tab switch: jump
      track.style.transition = "none";
      track.style.transform = `translateX(${(-(elapsed - W) * u).toFixed(2)}px)`;
      track.getBoundingClientRect();
      track.style.transition = "";
    } else {
      track.style.transform = `translateX(${(-(elapsed - W) * u).toFixed(2)}px)`;
    }
    track._t = elapsed;
    summary.push(`${vid}: ${cur || "no data"}`);
  }
  root.setAttribute("aria-label", `Feed status over the last ${W} seconds. Now: ${summary.join(", ")}`);
}

function renderStrategy(s) {
  const st = s.strategy;
  const sig = st.signal_bp;
  $("#sig-val").textContent = sig === null ? "—" : (sig >= 0 ? "+" : "−") + (Math.abs(sig) / 10000).toFixed(2);
  $("#sig-target").textContent = st.target === null ? "hold" : signed(st.target);
  const m = $("#sig-marker");
  m.style.opacity = sig === null ? 0.25 : 1;
  m.style.left = `${sig === null ? 50 : 50 + (sig / 10000) * 50}%`;
  const L = s.ledger;
  $("#pos-marker").style.left = `${50 + (L.position / st.q_max) * 50}%`;
  $("#led-pos").textContent = signed(L.position);
  $("#led-max").textContent = `${n(L.max_abs_position)} / ${st.q_max}`;
  $("#led-fees").textContent = `${n(L.fees_ticks)} ticks`;
  const total = Object.values(st.decisions).reduce((a, b) => a + b, 0) || 1;
  const host = $("#decisions");
  if (!host.childElementCount) {
    for (const [k, label] of DECISIONS) {
      host.append(el("div", { class: "bar-row", "data-d": k }, [el("span", { text: label }), el("div", { class: "track" }, [el("i")]), el("span", { class: "n", text: "0" })]));
    }
  }
  for (const [k] of DECISIONS) {
    const row = $(`[data-d="${k}"]`, host), v = st.decisions[k] || 0;
    row.hidden = !v && (k === "cooldown" || k === "halted_unknown");
    $("i", row).style.width = `${(v / total) * 100}%`;
    $(".n", row).textContent = n(v);
  }
}

function renderRoute(p) {
  const host = $("#route");
  host.replaceChildren();
  for (const side of ["buy", "sell"]) {
    const d = p[side];
    const to = d.venue
      ? el("span", { class: "route-to" }, [document.createTextNode("→ venue "), el("b", { text: d.venue }), document.createTextNode(` @ ${n(d.price)} ticks`)])
      : el("span", { class: "route-to", text: "→ no eligible venue (nothing would be sent)" });
    const ex = el("div", { class: "excluded" }, d.excluded.length
      ? d.excluded.map(([v, r]) => el("span", { class: "ex-chip", text: `${v} excluded · ${REASONS[r] || r}` }))
      : [el("span", { class: "hint", text: "no venue excluded" })]);
    host.append(el("div", { class: "route-row" }, [el("div", { class: "route-main" }, [el("span", { class: `side-tag ${side}`, text: `${cap(side)} 1` }), to]), ex]));
  }
}

function eventTone(kind, text) {
  if (kind === "status") return / fresh/.test(text.split("→")[1] || "") ? "good" : /stale/.test(text.split("→")[1] || "") ? "warn" : "bad";
  return { connect: "good", disconnect: "bad", order: "accent", unknown: "unknown", lost: "bad", fault: "warn" }[kind] || "neutral";
}
const EVENT_GROUP = { status: "feed", connect: "feed", disconnect: "feed", order: "orders", unknown: "orders", lost: "orders", fault: "fault" };

function renderEvents(s) {
  const host = $("#events");
  const rows = s.events.filter(([, , k]) => state.filter === "all" || EVENT_GROUP[k] === state.filter);
  const frag = document.createDocumentFragment();
  for (const [t, vid, kind, text] of rows) {
    const at = s.elapsed_s + t, key = `${vid}|${kind}|${text}`, seen = state.seenEvents.get(key) || [];
    const li = el("li", { "data-tone": eventTone(kind, text) }, [
      el("span", { class: "t", text: `${t.toFixed(1)} s` }), el("span", { class: "v", text: vid }),
      el("span", { class: "k", text: cap(kind) }), el("span", { class: "msg", text })]);
    if (!seen.some((x) => Math.abs(x - at) < 0.05)) { li.classList.add("new"); seen.push(at); state.seenEvents.set(key, seen); }
    frag.append(li);
  }
  if (!rows.length) frag.append(el("li", { class: "empty-row", text: "No events yet." }));
  host.replaceChildren(frag);
  if (state.seenEvents.size > 2000) state.seenEvents = new Map([...state.seenEvents].slice(-500));
}

// ---------- render: orders ----------
function renderOrders(s) {
  const c = s.counters;
  const stillUnknown = s.unresolved.filter(([, st]) => st === "UNKNOWN").length;
  const lostPending = s.unresolved.filter(([, st]) => st === "LOST_RECORD").length;
  const toUnknown = c.timeouts + c.unknown_disconnect;
  const fromUnknown = Math.max(0, toUnknown - stillUnknown - lostPending);
  renderLifecycle({ sent: c.sent, resolved: c.resolved_by_exec + fromUnknown, unknown: stillUnknown, lost: lostPending,
    e_exec: c.resolved_by_exec, e_unknown: toUnknown, e_status: fromUnknown, e_lost: c.lost_record, inflight: s.strategy.outstanding ? 1 : 0 });
  renderRecon(s.reconciliation, s.unresolved);
  renderRtt(s.exec_rtt);
  const L = s.ledger;
  $("#ledger-stats").replaceChildren(...[["Position", signed(L.position)], ["Max |position|", n(L.max_abs_position)], ["Cash", `${signed(L.cash_ticks)}`],
    ["Fees", n(L.fees_ticks)], ["Realized (pre-fee)", signed(L.realized_gross_ticks)], ["Status queries", n(c.status_queries)]]
    .map(([k, v]) => el("div", {}, [el("dt", { text: k }), el("dd", { class: "mono", text: v })])));
  $("#feed-rtt").replaceChildren(...Object.entries(s.feed_rtt).map(([v, r]) => el("div", {}, [el("dt", { text: `Venue ${v}` }), el("dd", { class: "mono", text: us(r.p50) })])));
  const rows = s.orders.map((o) => el("tr", {}, [
    el("td", { class: "mono", text: o.oid }),
    el("td", {}, [el("span", { class: `side-tag ${o.side}`, text: cap(o.side) })]),
    el("td", { class: "num", text: n(o.qty) }), el("td", { class: "num", text: n(o.limit) }),
    el("td", {}, [el("span", { class: "pill", "data-tone": o.status === "FILLED" ? "good" : "warn", text: o.status === "FILLED" ? "Filled" : `Rejected · ${REASONS[o.reason] || o.reason || "—"}` })]),
    el("td", { class: "num", text: n(o.price) }), el("td", { class: "num", text: n(o.fee) }),
    el("td", {}, [el("span", { class: "pill", "data-tone": o.via === "exec" ? "neutral" : "unknown", text: o.via === "exec" ? "Execution report" : cap(o.via.replace(/_/g, " ")) })]),
  ]));
  $("#orders-rows").replaceChildren(...(rows.length ? rows : [el("tr", {}, [el("td", { colspan: 8, class: "hint", text: "No resolved orders yet." })])]));
}

function renderLifecycle(v) {
  const host = $("#lifecycle");
  const nodes = [
    ["sent", "Sent", 20, 85, "accent"], ["resolved", "Resolved", 480, 12, "good"],
    ["unknown", "Unknown", 250, 145, "unknown"], ["lost", "Lost record", 480, 145, "bad"]];
  const edges = [
    ["e_exec", "M150,100 C300,100 330,42 480,42", "execution report", 300, 62],
    ["e_unknown", "M150,130 C200,130 200,175 250,175", "timeout / disconnect", 120, 172],
    ["e_status", "M380,160 C430,160 430,58 480,58", "status query", 440, 120],
    ["e_lost", "M380,180 L480,180", "venue restarted", 430, 200]];
  if (!host.childElementCount) {
    for (const [k, d, label, x, y] of edges) {
      host.append(svg("path", { class: "edge", d, "data-e": k, "marker-end": "url(#arrow)" }));
      const g = svg("text", { class: "elabel", x, y, "text-anchor": "middle" });
      g.append(svg("tspan", { class: "ecount", "data-ec": k }, "0"), svg("tspan", {}, ` ${label}`));
      host.append(g);
    }
    const defs = svg("defs");
    const mk = svg("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" });
    mk.append(svg("path", { d: "M0,0 L10,5 L0,10 z", fill: "var(--border-strong)" }));
    defs.append(mk);
    host.prepend(defs);
    for (const [k, name, x, y, tone] of nodes) {
      const g = svg("g", { class: "node", "data-tone": tone, transform: `translate(${x},${y})` });
      g.append(svg("rect", { width: 130, height: 62, rx: 10 }), svg("text", { class: "name", x: 14, y: 22 }, name), svg("text", { class: "count", x: 14, y: 50, "data-nc": k }, "0"));
      host.append(g);
    }
  }
  for (const [k] of nodes) setNum($(`[data-nc="${k}"]`, host), v[k]);
  for (const [k] of edges) {
    const prev = state.lastLife[k] ?? v[k];
    $(`[data-ec="${k}"]`, host).textContent = n(v[k]);
    $(`[data-e="${k}"]`, host).classList.toggle("flow", v[k] > prev || (k === "e_exec" && v.inflight > 0 && state.snap?.state === "running"));
    state.lastLife[k] = v[k];
  }
}

const ICON_OK = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3.5 8.5 3 3 6-7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const ICON_X = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
function renderRecon(r, unresolved) {
  setNum($("#recon-venue"), r.venue_fills);
  setNum($("#recon-ledger"), r.ledger_fills);
  const checks = [["Mismatched price / qty / fee", r.mismatched, r.mismatched === 0],
    ["Venue fills not in ledger", r.unbooked_venue_fills, r.unbooked_venue_fills === 0 || r.unbooked_all_unresolved],
    ["Ledger fills not at venue", r.ledger_not_at_venue, r.ledger_not_at_venue === 0]];
  $("#recon-checks").replaceChildren(...checks.map(([k, v, ok]) => {
    const tone = v === 0 ? "good" : ok ? "warn" : "bad";
    const dt = el("dt");
    dt.innerHTML = (v === 0 ? ICON_OK : ICON_X) + `<span></span>`;
    $("span", dt).textContent = k;
    return el("div", { class: "check", "data-tone": tone }, [dt, el("dd", { text: n(v) })]);
  }));
  const clean = r.mismatched === 0 && r.unbooked_venue_fills === 0 && r.ledger_not_at_venue === 0;
  const explained = r.mismatched === 0 && r.ledger_not_at_venue === 0 && r.unbooked_all_unresolved;
  setTone($("#recon-badge"), clean ? "good" : explained ? "warn" : "bad", clean ? "Reconciled" : explained ? "Explained gap" : "Mismatch");
  const note = $("#recon-note");
  note.hidden = clean;
  if (!clean) {
    note.dataset.tone = explained ? "warn" : "bad";
    note.textContent = explained
      ? `${n(r.unbooked_venue_fills)} venue fill(s) are not booked, and every one belongs to an unresolved order (${unresolved.map(([o]) => o).join(", ")}). The venue lost its record, so the strategy cannot know the outcome; it stays halted instead of guessing. An explained gap is identified uncertainty, not resolved accounting.`
      : "The ledger and the venue disagree on a fill that is not explained by an unresolved order.";
  }
}

function renderRtt(r) {
  $("#rtt-stats").replaceChildren(...[["Samples", n(r.count)], ["p50", us(r.p50)], ["p99", us(r.p99)], ["Max", us(r.max)]]
    .map(([k, v]) => el("div", {}, [el("dt", { text: k }), el("dd", { class: "mono", text: v })])));
  const host = $("#rtt-hist");
  host.replaceChildren();
  const { edges_us: e, counts } = r.hist;
  const lo = Math.log10(e[0]), hi = Math.log10(e[e.length - 1]);
  const X = (u) => 40 + ((Math.log10(u) - lo) / (hi - lo)) * 580;
  const max = Math.max(1, ...counts);
  for (const [u, label] of [[10, "10 µs"], [100, "100 µs"], [1000, "1 ms"], [10000, "10 ms"]]) {
    host.append(svg("line", { class: "chart-grid", x1: X(u), x2: X(u), y1: 10, y2: 150 }), svg("text", { class: "chart-axis", x: X(u), y: 168, "text-anchor": "middle" }, label));
  }
  counts.forEach((cnt, i) => {
    const h = (cnt / max) * 130;
    const rect = svg("rect", { class: "chart-bar", x: X(e[i]) + 1, width: Math.max(1, X(e[i + 1]) - X(e[i]) - 2), y: 150 - h, height: h, rx: 2 });
    rect.append(svg("title", {}, `${e[i]}–${e[i + 1]} µs: ${n(cnt)}`));
    host.append(rect);
  });
  for (const [k, col] of [["p50", "var(--accent)"], ["p99", "var(--warn)"]]) {
    if (r[k] === undefined) continue;
    const x = X(Math.max(e[0], r[k] / 1000));
    host.append(svg("line", { class: "chart-mark", x1: x, x2: x, y1: 8, y2: 150, stroke: col }), svg("text", { class: "chart-mark-label", x: x + 4, y: 18, fill: col }, `${k} ${us(r[k])}`));
  }
}

// ---------- quant ----------
const CELL_NAMES = { "1_no_trade": "No trade", "2_null": "Null", "3_controlled": "Controlled", "4_controlled_delay": "Controlled + delay", "5_controlled_fault": "Controlled + faults" };
function cellSetup(c) {
  if (!c.trade) return "no orders";
  const parts = [c.beta_pct ? "planted signal (β = 0.5)" : "no signal (β = 0)"];
  if (c.exec_delay_steps) parts.push(`${c.exec_delay_steps}-step execution delay`);
  if (c.outage_every) parts.push(`${c.outage_len}-step outage every ${c.outage_every}, ${c.drop_ack_pct}% acks dropped`);
  return parts.join(", ");
}
const pm = (s) => (s ? `${signed(Math.round(s.mean))} ± ${n(Math.round(s.stdev))}` : "—");

async function loadQuant() {
  state.quantLoaded = true;
  try {
    const [q, r] = await Promise.all([api("/api/quant"), api("/api/replay")]);
    if (q.available) renderQuant(q);
    else $("#matrix-rows").replaceChildren(el("tr", {}, [el("td", { colspan: 7, class: "hint", text: "Published matrix not found in docs/evidence/." })]));
    renderReplay(r);
    reveal($(".grid-quant"));
  } catch (err) {
    state.quantLoaded = false;
    toast(err.message, "bad");
  }
}

function renderQuant(q) {
  $("#matrix-src").textContent = `${q.seeds.length} seeds × ${n(q.steps)} steps · ${q.source}`;
  $("#matrix-rows").replaceChildren(...q.cells.map((c) => el("tr", c.cell === "3_controlled" ? { style: "background: rgba(66,232,208,0.06)" } : {}, [
    el("td", { text: `${c.cell[0]} · ${CELL_NAMES[c.cell] || c.cell}` }),
    el("td", { class: "hint", text: cellSetup(c) }),
    el("td", { class: "num", text: n(Math.round(c.fills)) }),
    el("td", { class: `num ${c.gross.mean < 0 ? "neg" : ""}`, text: pm(c.gross) }),
    el("td", { class: `num ${c.net.mean < 0 ? "neg" : ""}`, text: pm(c.net) }),
    el("td", { class: "num", text: c.hit_rate.rate === null ? "—" : c.hit_rate.rate.toFixed(3) }),
    el("td", { class: "num", text: `${c.open_position_seeds} / ${q.seeds.length}` }),
  ])));
  const p = q.paired_stats;
  $("#paired-sum").textContent = `mean ${signed(Math.round(p.mean))} ± ${n(Math.round(p.stdev))} ticks · positive on ${Math.round(p.frac_positive * p.n)}/${p.n} seeds`;
  const host = $("#paired");
  host.replaceChildren();
  const ys = q.paired.map((d) => d.diff), top = Math.max(...ys) * 1.1, bot = Math.min(0, ...ys);
  const Y = (v) => 190 - ((v - bot) / (top - bot)) * 170;
  const X = (i) => 60 + (i / (q.paired.length - 1)) * 560;
  for (const t of [0, 500, 1000, 1500, 2000].filter((t) => t <= top)) {
    host.append(svg("line", { class: "chart-grid", x1: 60, x2: 620, y1: Y(t), y2: Y(t), ...(t === 0 ? { stroke: "var(--bad)", "stroke-dasharray": "4 4" } : {}) }),
      svg("text", { class: "chart-axis", x: 52, y: Y(t) + 4, "text-anchor": "end" }, n(t)));
  }
  host.append(svg("line", { class: "chart-mark", x1: 60, x2: 620, y1: Y(p.mean), y2: Y(p.mean), stroke: "var(--brand)" }),
    svg("text", { class: "chart-mark-label", x: 620, y: 12, "text-anchor": "end", fill: "var(--brand)" }, `- - mean ${signed(Math.round(p.mean))}`));
  q.paired.forEach((d, i) => {
    const c = svg("circle", { class: "dot-pt", cx: X(i), cy: Y(d.diff), r: 4.5 });
    if (!reduceMotion) c.style.cssText = `opacity:0;animation:rise 360ms var(--ease-in) ${i * 18}ms both`;
    c.append(svg("title", {}, `seed ${d.seed}: ${signed(d.diff)} ticks`));
    host.append(c);
  });
  host.append(svg("text", { class: "chart-axis", x: 60, y: 212 }, `seed ${q.paired[0].seed}`), svg("text", { class: "chart-axis", x: 620, y: 212, "text-anchor": "end" }, `seed ${q.paired.at(-1).seed}`));
}

function renderReplay(r) {
  const host = $("#replay");
  if (!r.available) return host.replaceChildren(el("p", { class: "hint", text: "No replay results in docs/realdata-results/." }));
  const names = { fee0_delay0: "No fee", fee10bp_delay0: "10 bp fee", fee10bp_delay1: "10 bp fee + 1-event delay" };
  const usd = (v) => (v === null || v === undefined ? "—" : `${Number(v) < 0 ? "−" : ""}$${Math.abs(Number(v)).toFixed(2)}`);
  const grid = el("div", { class: "replay-grid" }, Object.entries(r.scenarios).map(([k, s]) => el("div", { class: "scn" }, [
    el("h3", { text: names[k] || k }),
    el("dl", {}, [["Orders", n(s.orders)], ["Filled", n(s.filled)], ["Size too small", n(s.rejected?.insufficient_quantity ?? 0)],
      ["Hit rate", s.hit_rate?.toFixed(3) ?? "—"],
      ["Pre-fee P&L (hypothetical terminal liquidation value)", usd(s.pre_fee_terminal_liquidation_value_pnl_usd)],
      ["Post-fee P&L (hypothetical terminal liquidation value)", usd(s.post_fee_terminal_liquidation_value_pnl_usd)],
      ["Hypothetical closing fee (in post-fee)", usd(s.hypothetical_liquidation_fee_usd)],
      ["Final position", `${signed(s.final_position_lots)} lots (${s.final_position_btc} BTC)`],
      ["Unresolved orders at end", n(s.orders_unresolved_at_end)]].map(([a, b]) => el("div", {}, [el("dt", { text: a }), el("dd", { text: b })]))),
  ])));
  const cap = r.capture, cbt = cap.counts_by_type;
  host.replaceChildren(grid, el("p", { class: "fine", text:
    `${cap.product} public order-book feed · ${cap.provenance} capture started ${cap.started_utc} · raw SHA-256 ${cap.raw_sha256.slice(0, 8)}… · ${cap.seconds} s · ${n(cap.messages)} messages (${n(cbt.snapshot)} snapshot, ${n(cbt.l2update)} updates, ${n(cbt.heartbeat)} heartbeats) · ${n(cap.reconnects)} reconnects. One historical 10-minute sample; lots of 0.001 BTC, position limit 0.005 BTC. P&L values the open position at the last valid touch with no closing trade made; post-fee value includes a hypothetical closing fee. Raw data is not redistributed.` }));
}

async function runJob(name, onDone, btn) {
  btn.disabled = true;
  try {
    await api(`/api/jobs/${name}`, {});
    for (;;) {
      await new Promise((r) => setTimeout(r, 700));
      const j = await api(`/api/jobs/${name}`);
      if (j.state === "done") return onDone(j.result);
    }
  } catch (err) {
    toast(err.message, "bad");
  } finally {
    btn.disabled = false;
  }
}

function reproduce() {
  const out = $("#repro-out");
  out.textContent = "Running 5 cells × 30 seeds…";
  runJob("reproduce", (r) => {
    if (r.error) { out.textContent = `Failed: ${r.error}`; return; }
    out.innerHTML = "";
    out.append(el("span", { class: "pill", "data-tone": r.ok ? "good" : "bad", text: r.ok ? "Match" : "Different" }),
      document.createTextNode(r.ok ? ` All ${r.compared_keys.length} keys equal the published source (run ID excluded) · ${r.seconds} s`
        : ` Differs in: ${r.differing_keys.join(", ")}`));
  }, $("#repro-btn"));
}

// ---------- evidence ----------
async function loadEvidence() {
  state.evidenceLoaded = true;
  try {
    const e = await api("/api/evidence");
    renderPerf(e.perf_runs);
    $("#hash-rows").replaceChildren(...e.hashes.map((h) => el("tr", { "data-file": h.file }, [
      el("td", { class: "mono", text: h.file }),
      el("td", { class: "mono hash", title: h.sha256, text: `${h.sha256.slice(0, 16)}…${h.sha256.slice(-8)}` }),
      el("td", {}, [el("span", { class: "pill", "data-tone": "neutral", text: "Not checked" })])])));
    reveal($(".grid-evidence"));
  } catch (err) {
    state.evidenceLoaded = false;
    toast(err.message, "bad");
  }
}

function renderPerf(runs) {
  const lo = Math.log10(10), hi = Math.log10(1000);
  const P = (ns) => `${Math.min(100, Math.max(0, ((Math.log10(ns / 1000) - lo) / (hi - lo)) * 100))}%`;
  $("#perf-rows").replaceChildren(...runs.map((r, i) => {
    const x = r.exec_rtt_ns;
    const bar = el("div", { class: "perfbar", title: "log scale, 10 µs – 1 ms" });
    bar.append(el("span", { class: "rng", style: `left:${P(x.p50)};width:calc(${P(x.max)} - ${P(x.p50)})` }));
    for (const [k, col] of [["p50", "var(--accent)"], ["p99", "var(--warn)"], ["max", "var(--bad)"]]) bar.append(el("span", { class: "p", style: `left:${P(x[k])};background:${col}` }));
    return el("tr", {}, [el("td", { text: `Run ${i + 1}` }), el("td", { class: "num", text: `${n(r.filled)} / ${n(r.sent)}` }),
      el("td", { class: "num", text: us(x.p50) }), el("td", { class: "num", text: us(x.p99) }), el("td", { class: "num", text: us(x.max) }),
      el("td", {}, [bar]), el("td", { class: "num", text: n(r.mismatched) })]);
  }));
}

function verify() {
  runJob("verify", (r) => {
    for (const f of r.files) {
      const row = $(`#hash-rows tr[data-file="${CSS.escape(f.file)}"]`);
      if (!row) continue;
      const pill = $(".pill", row);
      pill.dataset.tone = f.match ? "good" : "bad";
      pill.textContent = f.match ? "Matches" : f.actual === null ? "Missing" : "Mismatch";
    }
    $("#hash-status").textContent = r.all_match ? `All ${r.files.length} files match.` : "Some files do not match.";
  }, $("#verify-btn"));
}

function runTests() {
  const b = $("#tests-status");
  setTone(b, "accent", "Running…");
  $("#tests-summary").textContent = "Running the full suite…";
  runJob("tests", (r) => {
    if (r.error) { setTone(b, "bad", "Error"); $("#tests-summary").textContent = r.error; return; }
    $("#tests-count").classList.add("has-value");
    tween($("#tests-count"), r.ran);
    setTone(b, r.ok ? "good" : "bad", r.ok ? "Passing" : "Failing");
    $("#tests-summary").textContent = `${r.summary} · ${r.seconds} s`;
  }, $("#tests-btn"));
}

// ---------- plain-English status ----------
function renderStatus(s) {
  const A = s.venues?.A, c = s.counters;
  const unknown = s.unresolved.filter(([, st]) => st === "UNKNOWN").map(([o]) => o);
  const lost = s.unresolved.filter(([, st]) => st === "LOST_RECORD").map(([o]) => o);
  let tone, text, why;
  if (s.error) {
    [tone, text, why] = ["bad", `The run stopped with an error: ${s.error}`, "See the terminal running the console for details."];
  } else if (s.state === "complete") {
    const r = s.reconciliation;
    [tone, text] = r && r.mismatched === 0 && r.ledger_not_at_venue === 0 && (r.unbooked_venue_fills === 0 || r.unbooked_all_unresolved)
      ? ["good", `Run finished: ${n(c.filled)} fills, books match the venue${r.unbooked_venue_fills ? " (gap explained by an unresolved order)" : ""}.`]
      : ["bad", "Run finished, but the books do not match the venue. See Orders."];
    why = "At the end, QuoteGate compares every fill the venue recorded with every fill the strategy booked: same order, quantity, price and fee.";
  } else if (s.state === "settling") {
    [tone, text, why] = ["accent", "Ending the run: no new orders, letting in-flight orders resolve.", "Orders already sent may still be answered. Waiting a moment means the final audit includes them."];
  } else if (s.trading === "halted_lost_record") {
    [tone, text] = ["bad", `Halted: venue A restarted and forgot order ${lost.join(", ")}. QuoteGate will not guess whether it executed.`];
    why = "The venue no longer has a record of the order, so nobody can say whether it filled. Guessing either way could leave the books wrong. A real desk would escalate to a person; QuoteGate stays halted and the audit reports the gap.";
  } else if (s.trading === "halted_unknown") {
    [tone, text] = ["unknown", `Waiting: the outcome of order ${unknown.join(", ")} is unknown. Asking the venue (${n(c.status_queries)} status queries so far).`];
    why = "The confirmation never arrived, so the order may or may not have executed. Sending it again could buy twice; ignoring it could leave a position nobody tracks. So trading pauses until the venue answers.";
  } else if (s.trading === "no_signal") {
    text = A.status === "stale" ? `Paused: venue A's last price is ${A.age_ms?.toFixed(0)} ms old (limit ${s.stale_ms} ms).`
      : `Paused: venue A is ${(STATUS[A.status] || STATUS.no_quote).label.toLowerCase()}.`;
    [tone, why] = ["warn", "Trading on an old price risks buying or selling at a price that no longer exists. QuoteGate waits for a fresh quote instead of guessing."];
  } else {
    [tone, text] = ["good", `Trading normally: venue A's price is ${A.age_ms?.toFixed(1)} ms old, under the ${s.stale_ms} ms limit.`];
    why = "The strategy trades only when venue A's latest price is fresh, no order outcome is unknown, and the position stays within ±5.";
  }
  const strip = $("#status-strip");
  if (strip.dataset.tone !== tone) setTone(strip, tone);
  $("#status-text").textContent = text;
  $("#status-why-text").textContent = why;
}

// ---------- guided demo ----------
const GUIDE = [
  { kind: "pause_feed_1s", title: "Freeze venue A's prices for 1 second",
    desc: "A real feed can stall. Expect: within 50 ms venue A is marked stale, trading pauses, and the router stops using it. Then it recovers." },
  { kind: "drop_next_ack", title: "Lose an order confirmation",
    desc: "The venue executes an order but its reply never arrives. Expect: the order goes UNKNOWN, trading halts, QuoteGate asks the venue, and books the answer. It never resends." },
  { kind: "restart_mid_order", title: "Restart the venue in the middle of an order",
    desc: "The venue forgets its orders. Expect: the order can't be resolved, so QuoteGate stays halted instead of guessing, and the audit explains the gap." },
];
const STATE_TEXT = { pending: "Watching for the expected outcome…", handled: "✓ Passed", not_observed: "Not observed",
  skipped: "Skipped" };

function renderGuide(s) {
  const card = $("#guide");
  card.hidden = !state.guide;
  if (!state.guide) return;
  const recs = Object.fromEntries((s.faults || []).map((f) => [f.id, f]));
  let passed = 0, unlocked = true;
  const host = $("#guide-steps");
  if (!host.childElementCount) {
    GUIDE.forEach((g, i) => {
      const li = el("li", { class: "guide-step", "data-i": i }, [
        el("span", { class: "num", text: String(i + 1) }),
        el("div", {}, [el("h3", { text: g.title }), el("p", { text: g.desc }), el("p", { class: "result" })]),
        el("button", { class: "btn btn-sm", text: "Do it" })]);
      $("button", li).addEventListener("click", async () => {
        const r = await fault(g.kind, "A");
        if (r) { state.guide.ids[i] = r.id; poll(true); }
      });
      host.append(li);
    });
    const fin = el("li", { class: "guide-step", "data-i": "finish" }, [
      el("span", { class: "num", text: "✓" }),
      el("div", {}, [el("h3", { text: "Finish and see the scorecard" }), el("p", { text: "Stops the run, waits for in-flight orders, and audits every fill." })]),
      el("button", { class: "btn btn-primary btn-sm", text: "Finish run" })]);
    $("button", fin).addEventListener("click", () => toggleRun());
    host.append(fin);
  }
  GUIDE.forEach((g, i) => {
    const li = $(`[data-i="${i}"]`, host), rec = recs[state.guide.ids[i]];
    const st = rec ? rec.state : unlocked ? "ready" : "locked";
    li.dataset.state = st;
    const res = $(".result", li);
    res.dataset.tone = { handled: "good", pending: "accent", not_observed: "warn", skipped: "warn" }[st] || "neutral";
    res.textContent = rec ? `${STATE_TEXT[rec.state]}${rec.observed_ms !== null ? ` in ${rec.observed_ms} ms` : ""}${rec.detail ? ` — ${rec.detail}` : ""}` : "";
    const btn = $("button", li);
    btn.textContent = rec && rec.state !== "handled" && rec.state !== "pending" ? "Try again" : "Do it";
    btn.disabled = s.state !== "running" || !unlocked || st === "pending" || st === "handled";
    if (st === "handled") passed += 1;
    unlocked = unlocked && st === "handled";
  });
  const fin = $('[data-i="finish"]', host);
  fin.dataset.state = s.state === "complete" ? "handled" : unlocked ? "ready" : "locked";
  $("button", fin).disabled = !unlocked || s.state !== "running";
  $("#guide-progress").textContent = `${passed} of ${GUIDE.length} checks passed`;
}

// ---------- fault outcomes ----------
function renderFaultResults(s) {
  const latest = {};
  for (const f of s.faults || []) latest[f.kind] = f;
  $$("[data-fault]").forEach((b) => {
    const li = b.closest("li"), f = latest[b.dataset.fault];
    let out = $(".fault-result", li);
    if (!f) { out?.remove(); return; }
    if (!out) { out = el("span", { class: "fault-result" }); $("div", li).append(out); }
    out.dataset.tone = { handled: "good", pending: "accent", not_observed: "warn", skipped: "warn" }[f.state];
    out.textContent = `${STATE_TEXT[f.state]}${f.observed_ms !== null ? ` in ${f.observed_ms} ms` : ""}${f.detail ? ` — ${f.detail}` : ""}`;
  });
}

function announceFaults(faults) {
  for (const f of faults) {
    const prev = state.faultStates[f.id];
    state.faultStates[f.id] = f.state;
    if (prev === "pending" && f.state !== "pending") {
      toast(f.state === "handled" ? `✓ Handled in ${f.observed_ms} ms: ${f.detail}` : `${STATE_TEXT[f.state]}: ${f.detail}`,
        f.state === "handled" ? "" : "bad");
    }
  }
}

// ---------- scorecard ----------
function showScorecard(s) {
  const c = s.counters, r = s.reconciliation, faults = s.faults || [];
  const handled = faults.filter((f) => f.state === "handled").length;
  const clean = r.mismatched === 0 && r.ledger_not_at_venue === 0 && r.unbooked_venue_fills === 0;
  const explained = !clean && r.mismatched === 0 && r.ledger_not_at_venue === 0 && r.unbooked_all_unresolved;
  $("#sc-sub").textContent = `seed ${s.seed} · ${s.elapsed_s?.toFixed(0)} s`;
  const pnl = s.pnl?.equity_ticks;
  const tiles = [["Orders sent", n(c.sent)], ["Filled", n(c.filled)],
    ["Books vs venue", clean ? "Match" : explained ? "Gap explained" : "Mismatch"],
    ["Faults handled", `${handled} / ${faults.length}`], ["Final position", signed(s.ledger.position)],
    ["Max |position|", `${n(s.ledger.max_abs_position)} / 5`],
    ["P&L after fees", pnl === null || pnl === undefined ? "—" : `${signed(pnl)} ticks`],
    ["Unknown / lost", n(s.unresolved.length)]];
  const verdict = clean || explained
    ? `Every fill accounted for${explained ? " (one gap, explained by an order the venue forgot)" : ""}. ${handled} of ${faults.length} injected faults handled as expected.`
    : "The books and the venue disagree. Open the Orders tab for details.";
  const body = $("#sc-body");
  body.replaceChildren(
    el("p", { class: "sc-verdict", text: verdict }),
    el("dl", { class: "sc-grid" }, tiles.map(([k, v]) => el("div", { class: "sc-tile" }, [el("dt", { text: k }), el("dd", { text: v })]))),
    ...(faults.length ? [el("div", { class: "label", text: "Faults injected" }), el("ul", { class: "sc-faults" }, faults.map((f) => {
      const tone = f.state === "handled" ? "good" : "warn";
      return el("li", { "data-tone": tone }, [el("span", { class: "ic", text: f.state === "handled" ? "✓" : "!" }),
        el("div", {}, [el("b", { text: `${f.kind.replace(/_/g, " ")} (venue ${f.venue})` }), document.createElement("br"),
          el("small", { text: `${STATE_TEXT[f.state]}${f.observed_ms !== null ? ` in ${f.observed_ms} ms` : ""}${f.detail ? ` — ${f.detail}` : ""}` })])]);
    }))] : []),
    el("p", { class: "fine", text: "P&L is cash plus the open position valued at venue A's last bid (long) or ask (short): hypothetical, no closing trade. Synthetic market; nothing here is real money." }));
  state.lastReport = s;
  const dlg = $("#scorecard");
  if (!dlg.open) dlg.showModal();
}

function saveFile(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = el("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function downloadReport() {
  const s = state.lastReport;
  if (!s) return;
  const { timeline, ...rest } = s;
  saveFile(`quotegate-run-seed${s.seed}.json`, JSON.stringify({ generated: new Date().toISOString(), ...rest }, null, 1), "application/json");
}

// ---------- check fills ----------
const FIELD_LABEL = { order_id: "Order ID", side: "Side", quantity: "Quantity", price: "Price", fee: "Fee", time: "Time" };
const KIND_LABEL = { booked_twice: "Booked twice", missing_from_bot: "Missing from bot", not_at_broker: "Not at broker",
  mismatch: "Mismatch", position_limit: "Over limit" };
const KIND_TONE = { booked_twice: "bad", missing_from_bot: "bad", not_at_broker: "bad", mismatch: "warn", position_limit: "warn" };

function initFills() {
  for (const side of ["broker", "bot"]) {
    const card = $(`[data-file="${side}"]`), input = $('[data-f="input"]', card), drop = $(".drop", card);
    input.addEventListener("change", () => input.files[0] && readFile(side, input.files[0]));
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => {
      e.preventDefault();
      drop.classList.remove("over");
      if (e.dataTransfer.files[0]) readFile(side, e.dataTransfer.files[0]);
    });
  }
  $$("[data-sample]").forEach((b) => b.addEventListener("click", async () => {
    try {
      const smp = await api(`/api/fillcheck/sample?name=${b.dataset.sample}`);
      await loadText("broker", smp.broker_name, smp.broker_csv);
      await loadText("bot", smp.bot_name, smp.bot_csv);
      $("#fills-limit").value = String(smp.suggested_limit);
      $("#fills-mark").value = "";
      toast(`${b.textContent} loaded (see examples/fills/README.md for the planted problems). Press Check fills.`);
    } catch (err) { toast(err.message, "bad"); }
  }));
  $("#fills-run").addEventListener("click", runFillCheck);
}

function readFile(side, file) {
  if (file.size > 2_000_000) return toast(`${file.name} is larger than 2 MB.`, "bad");
  const rd = new FileReader();
  rd.onload = () => loadText(side, file.name, String(rd.result));
  rd.onerror = () => toast(`Could not read ${file.name}.`, "bad");
  rd.readAsText(file);
}

async function loadText(side, name, text) {
  const card = $(`[data-file="${side}"]`);
  try {
    const pv = await api("/api/fillcheck/preview", { csv: text });
    state.fills[side] = { name, text, mapping: pv.mapping };
    $('[data-f="name"]', card).textContent = name;
    renderMapping(side, pv);
  } catch (err) {
    state.fills[side] = null;
    $('[data-f="name"]', card).textContent = "Could not read file";
    toast(`${name}: ${err.message}`, "bad");
  }
  updateFillsReady();
}

function renderMapping(side, pv) {
  const host = $(`[data-file="${side}"] [data-f="mapping"]`);
  host.hidden = false;
  const grid = el("div", { class: "map-grid" }, pv.fields.map((f) => {
    const sel = el("select", { "aria-label": `${FIELD_LABEL[f]} column` }, [el("option", { value: "", text: "— none —" }),
      ...pv.headers.map((h) => el("option", { value: h, text: h }))]);
    sel.value = pv.mapping[f] || "";
    sel.addEventListener("change", () => { state.fills[side].mapping[f] = sel.value || null; updateFillsReady(); });
    const lab = el("span", {}, [document.createTextNode(FIELD_LABEL[f])]);
    if (pv.required.includes(f)) lab.append(el("b", { text: " *" }));
    return el("label", { class: "field" }, [lab, sel]);
  }));
  const table = el("table", { class: "table preview" }, [
    el("thead", {}, [el("tr", {}, pv.headers.map((h) => el("th", { scope: "col", text: h })))]),
    el("tbody", {}, pv.rows.slice(0, 3).map((r) => el("tr", {}, r.map((v) => el("td", { class: "mono", text: v })))))]);
  host.replaceChildren(el("div", { class: "label sub", text: "Columns (detected automatically; change if wrong)" }), grid,
    el("div", { class: "label sub", text: "First rows" }), el("div", { class: "table-wrap" }, [table]));
}

function updateFillsReady() {
  const missing = [];
  for (const [side, label] of [["broker", "broker export"], ["bot", "bot log"]]) {
    const f = state.fills[side];
    if (!f) { missing.push(`add the ${label}`); continue; }
    const need = ["order_id", "quantity", "price"].filter((k) => !f.mapping[k]).map((k) => FIELD_LABEL[k]);
    if (need.length) missing.push(`map ${need.join(", ")} in the ${label}`);
  }
  $("#fills-run").disabled = missing.length > 0;
  $("#fills-run-hint").textContent = missing.length ? `To check: ${missing.join("; ")}.` : "Ready.";
}

async function runFillCheck() {
  const btn = $("#fills-run");
  btn.disabled = true;
  try {
    const r = await api("/api/fillcheck", {
      broker_csv: state.fills.broker.text, bot_csv: state.fills.bot.text,
      broker_mapping: state.fills.broker.mapping, bot_mapping: state.fills.bot.mapping,
      max_position: $("#fills-limit").value.trim() || null, mark: $("#fills-mark").value.trim() || null });
    renderFillReport(r);
  } catch (err) {
    toast(err.message, "bad");
  } finally {
    updateFillsReady();
  }
}

function renderFillReport(r) {
  const host = $("#fills-report"), s = r.summary, e = r.economics;
  const problems = s.booked_twice + s.missing_from_bot + s.not_at_broker + s.mismatch + s.position_limit;
  const verdict = problems ? `${problems} problem${problems > 1 ? "s" : ""} found across ${n(s.broker_orders)} broker orders`
    : `All ${n(s.matched)} orders match`;
  const chips = [["Matched", s.matched, "good"], ["Booked twice", s.booked_twice, "bad"], ["Missing from bot", s.missing_from_bot, "bad"],
    ["Not at broker", s.not_at_broker, "bad"], ["Mismatched", s.mismatch, "warn"], ["Over position limit", s.position_limit, "warn"],
    ["Unreadable rows", s.invalid_rows, "warn"]].map(([k, v, tone]) =>
    el("div", { class: "rchip", "data-tone": v === 0 && tone !== "good" ? "neutral" : tone }, [el("dt", { text: k }), el("dd", { text: n(v) })]));
  const pos = r.positions;
  const econ = el("dl", { class: "stats2" }, [["Realized P&L before fees", e.realized_pre_fee], ["Fees paid", e.fees],
    ["Realized P&L after fees", e.realized_after_fees], ["Final position (broker)", e.final_position],
    ["Max |position|", e.max_abs_position], ...(e.unrealized_at_mark !== undefined ? [["Open position at mark", e.unrealized_at_mark]] : [])]
    .filter(([, v]) => v !== undefined).map(([k, v]) => el("div", {}, [el("dt", { text: k }), el("dd", { class: "mono", text: v ?? "—" })])));
  const rows = r.issues.map((i) => el("tr", {}, [
    el("td", {}, [el("span", { class: "pill", "data-tone": KIND_TONE[i.kind], text: KIND_LABEL[i.kind] })]),
    el("td", { class: "mono", text: i.order_id }), el("td", { class: "wrap", text: i.detail }),
    el("td", { class: "num", text: i.line ?? "—" }), el("td", { class: "issue-cause wrap", text: i.cause })]));
  const invalid = [...r.invalid.broker.map(([l, why]) => `broker line ${l}: ${why}`), ...r.invalid.bot.map(([l, why]) => `bot line ${l}: ${why}`)];
  const card = el("article", { class: "card" }, [
    el("div", { class: "report-head" }, [
      el("div", {}, [el("p", { class: "report-verdict", text: verdict }), el("p", { class: "fine", text: r.note })]),
      el("div", { class: "hash-actions" }, [el("button", { class: "btn", id: "fills-json", text: "Download JSON" }),
        el("button", { class: "btn", id: "fills-csv", text: "Download issues (CSV)" })])]),
    el("dl", { class: "report-chips" }, chips),
    el("div", { class: "report-cols" }, [
      el("div", {}, [el("div", { class: "label", text: "Final position" }),
        el("dl", { class: "stats2" }, [el("div", {}, [el("dt", { text: "Broker says" }), el("dd", { class: "mono", text: pos.broker })]),
          el("div", {}, [el("dt", { text: "Your bot's log says" }), el("dd", { class: "mono", text: pos.bot })])]),
        el("p", { class: "callout", "data-tone": pos.agree ? "good" : "bad", text: pos.agree ? "Positions agree."
          : "Positions disagree: your bot is trading on a position the broker doesn't hold." })]),
      el("div", {}, [el("div", { class: "label", text: `Economics from broker fills (${e.order_source || "file order"})` }), econ])]),
    el("div", { class: "label sub", text: "Issues" }),
    r.issues.length ? el("div", { class: "table-wrap" }, [el("table", { class: "table" }, [
      el("thead", {}, [el("tr", {}, ["Problem", "Order", "What differs", "Line", "Usual cause and fix"].map((h) => el("th", { scope: "col", text: h })))]),
      el("tbody", {}, rows)])]) : el("p", { class: "hint", text: "No issues." }),
    ...(invalid.length ? [el("div", { class: "label sub", text: "Rows skipped" }), el("ul", { class: "limits" }, invalid.slice(0, 20).map((t) => el("li", { text: t })))] : [])]);
  host.replaceChildren(card);
  host.hidden = false;
  $("#fills-json", host).addEventListener("click", () => saveFile("quotegate-fill-check.json", JSON.stringify(r, null, 1), "application/json"));
  $("#fills-csv", host).addEventListener("click", () => {
    const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const lines = ["problem,order_id,detail,line,cause", ...r.issues.map((i) => [KIND_LABEL[i.kind], i.order_id, i.detail, i.line, i.cause].map(q).join(","))];
    saveFile("quotegate-fill-issues.csv", lines.join("\n") + "\n", "text/csv");
  });
  reveal(host);
  card.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
}

// ---------- wiring ----------
$("#run-form").addEventListener("submit", toggleRun);
$$('[data-action="start"]').forEach((b) => b.addEventListener("click", toggleRun));
$$('[data-action="guided"]').forEach((b) => b.addEventListener("click", (e) => toggleRun(e, true)));
$("#status-why").addEventListener("click", () => {
  const open = $("#status-why").getAttribute("aria-expanded") === "true";
  $("#status-why").setAttribute("aria-expanded", String(!open));
  $("#status-why-text").hidden = open;
});
$("#sc-download").addEventListener("click", downloadReport);
initFills();
$$("[data-fault]").forEach((b) => b.addEventListener("click", () => fault(b.dataset.fault, "A")));
$$(".rail-item").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab, true)));
$$("[data-filter]").forEach((b) => b.addEventListener("click", () => {
  state.filter = b.dataset.filter;
  $$("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  if (state.snap?.events) renderEvents(state.snap);
}));
$("#repro-btn").addEventListener("click", reproduce);
$("#verify-btn").addEventListener("click", verify);
$("#tests-btn").addEventListener("click", runTests);

addEventListener("hashchange", () => {
  const t = location.hash.slice(1);
  if (["live", "fills", "orders", "quant", "evidence"].includes(t) && t !== state.tab) showTab(t);
});
const initial = location.hash.slice(1);
showTab(["live", "fills", "orders", "quant", "evidence"].includes(initial) ? initial : "live");
poll(true);
