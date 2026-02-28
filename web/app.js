/* global document, fetch */

const API_BASE = "/llm-manager-api";
const UI_BUILD = "dropdowns-v1"; // shown in UI so you can confirm cache-bust loaded

async function api(path, opts = {}) {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });

  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch (_) {}

  if (!res.ok) {
    const detail = json?.detail || json?.message || text || "";
    throw new Error(`HTTP ${res.status}${detail ? `: ${detail}` : ""}`);
  }
  return json ?? text;
}

function $(id) { return document.getElementById(id); }

function setStatus(msg, tone="info") {
  const box = $("statusBox");
  box.textContent = msg;
  box.className = `status ${tone}`;
}

function setOut(obj) {
  $("out").textContent = typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);
}

function basename(p) {
  if (!p || typeof p !== "string") return "";
  const s = p.replace(/\/+$/, "");
  return s.split("/").pop() || "";
}

function engineTone(listening, activeState) {
  if (activeState === "active" && listening) return "ok";
  if (activeState === "inactive" && !listening) return "warn";
  return "bad";
}

function engineLabel(listening, activeState) {
  if (activeState === "active" && listening) return { tone: "ok", text: "RUNNING + LISTENING" };
  if (activeState === "active" && !listening) return { tone: "warn", text: "RUNNING (NOT LISTENING)" };
  if (activeState === "inactive") return { tone: "warn", text: "STOPPED" };
  return { tone: "bad", text: "UNKNOWN" };
}

function fmtEngineMeta(e) {
  const u = e.unit || "(no unit)";
  const st = e.systemd?.ActiveState || e.systemd?.error || "unknown";
  const sub = e.systemd?.SubState ? `/${e.systemd.SubState}` : "";
  const pid = e.systemd?.MainPID ? ` pid=${e.systemd.MainPID}` : "";
  const listen = e.listening ? "listening" : "not-listening";
  return `${u} | ${st}${sub}${pid} | ${listen} | ${e.base}\n${e.active}`;
}

function setSelectOptions(selectEl, items, activeName) {
  if (!selectEl) return;
  const prev = selectEl.value;
  selectEl.innerHTML = "";
  const opt0 = document.createElement("option");
  opt0.value = "";
  opt0.textContent = items.length ? "— choose a model —" : "(no models found)";
  selectEl.appendChild(opt0);

  for (const name of items) {
    const o = document.createElement("option");
    o.value = name;
    o.textContent = name;
    selectEl.appendChild(o);
  }

  // Prefer: activeName, else preserve previous selection if still present
  const trySet = (v) => {
    if (!v) return false;
    const ok = [...selectEl.options].some(o => o.value === v);
    if (ok) { selectEl.value = v; return true; }
    return false;
  };

  if (trySet(activeName)) return;
  if (trySet(prev)) return;
  selectEl.value = "";
}

let MODEL_CACHE = null;

async function refreshModels() {
  const m = await api("/models");
  MODEL_CACHE = m;

  const activeChat = basename(m.active?.chat);
  const activeIntent = basename(m.active?.intent);
  const activeSmall = basename(m.active?.small);

  // Slot visibility — hide entire engine card when ENABLE_*=0
  const slots = m.slots_enabled || { chat: true, intent: true, small: true };
  const toggleSlot = (id, visible) => {
    const el = $(id);
    if (el) el.style.display = visible ? "" : "none";
  };
  toggleSlot("engineChat",   slots.chat);
  toggleSlot("engineIntent", slots.intent);
  toggleSlot("engineSmall",  slots.small);

  // Dashboard
  setSelectOptions($("selChat"),   m.chat || [],   activeChat);
  setSelectOptions($("selIntent"), m.intent || [], activeIntent);
  setSelectOptions($("selSmall"),  m.small || [],  activeSmall);

  // Engine cards
  setSelectOptions($("chatModel"),   m.chat || [],   activeChat);
  setSelectOptions($("intentModel"), m.intent || [], activeIntent);
  setSelectOptions($("smallModel"),  m.small || [],  activeSmall);
}

function setEngineButtonStates(prefix, activeState, listening) {
  // Start enabled when inactive. Stop enabled when active. Restart enabled when active.
  const isActive = activeState === "active";
  const startBtn = $(`${prefix}Start`);
  const stopBtn  = $(`${prefix}Stop`);
  const rstBtn   = $(`${prefix}Restart`);

  if (startBtn) startBtn.disabled = isActive;
  if (stopBtn)  stopBtn.disabled  = !isActive;
  if (rstBtn)   rstBtn.disabled   = !isActive;

  // Label the start button if already running
  if (startBtn) startBtn.textContent = isActive ? "Start (running)" : "Start";
  if (stopBtn)  stopBtn.textContent  = isActive ? "Stop" : "Stop (stopped)";
  if (rstBtn)   rstBtn.textContent   = isActive ? "Restart" : "Restart (stopped)";
}

async function refreshAll() {
  try {
    const hint = $("cacheHint");
    if (hint) {
      hint.textContent = `JS: ${UI_BUILD}`;
      hint.className = "pill ok";
      hint.title = "This proves the newest app.js loaded (cache-bust working).";
    }

    const h = await api("/health");
    setStatus(`OK — ${h.time}`, "ok");

    // Models first so dropdowns populate ASAP
    await refreshModels();

    // Engine status
    const s = await api("/engines/status");

    $("chatMeta").textContent = fmtEngineMeta(s.chat);
    $("intentMeta").textContent = fmtEngineMeta(s.intent);
    $("smallMeta").textContent = fmtEngineMeta(s.small);

    const chatLbl = engineLabel(s.chat.listening, s.chat.systemd?.ActiveState);
    const intentLbl = engineLabel(s.intent.listening, s.intent.systemd?.ActiveState);
    const smallLbl = engineLabel(s.small.listening, s.small.systemd?.ActiveState);

    $("chatState").textContent = chatLbl.text;
    $("chatState").className = `pill ${chatLbl.tone}`;
    $("intentState").textContent = intentLbl.text;
    $("intentState").className = `pill ${intentLbl.tone}`;
    $("smallState").textContent = smallLbl.text;
    $("smallState").className = `pill ${smallLbl.tone}`;

    $("engineChat").className = `card status ${engineTone(s.chat.listening, s.chat.systemd?.ActiveState)}`;
    $("engineIntent").className = `card status ${engineTone(s.intent.listening, s.intent.systemd?.ActiveState)}`;
    $("engineSmall").className = `card status ${engineTone(s.small.listening, s.small.systemd?.ActiveState)}`;

    setEngineButtonStates("chat", s.chat.systemd?.ActiveState, s.chat.listening);
    setEngineButtonStates("intent", s.intent.systemd?.ActiveState, s.intent.listening);
    setEngineButtonStates("small", s.small.systemd?.ActiveState, s.small.listening);

    // VRAM info (non-fatal)
    try {
      const v = await api("/vram");
      const vramEl = $("vramInfo");
      if (vramEl && v.gpus) {
        vramEl.textContent = v.gpus.map(g =>
          `GPU ${g.index}: ${g.name}  ${g.vram_used_mb}/${g.vram_total_mb} MB  (${g.gpu_util_pct}%)`
        ).join("  |  ");
      }
    } catch (_) { /* vram endpoint optional */ }
  } catch (e) {
    setStatus(`API error: ${e.message}`, "bad");
    const hint = $("cacheHint");
    if (hint) {
      hint.textContent = "JS: loaded (API error)";
      hint.className = "pill warn";
      hint.title = "JS loaded, but API call failed. Check /llm-manager-api/health in browser.";
    }
  }
}

async function runTest(mode) {
  const q = $("q").value.trim() || "Hello";
  const path = mode === "chat" ? `/test-chat?q=${encodeURIComponent(q)}`
            : mode === "intent" ? `/test-intent?q=${encodeURIComponent(q)}`
            : `/test-util?q=${encodeURIComponent(q)}`;
  try {
    setOut({ running: true, mode, q });
    setOut(await api(path));
  } catch (e) {
    setOut({ error: e.message, mode, q });
  } finally {
    await refreshAll();
  }
}

async function engineAction(mode, action) {
  try {
    setOut({ running: true, mode, action });
    setOut(await api(`/engines/${mode}/${action}`, { method: "POST" }));
  } catch (e) {
    setOut({ error: e.message, mode, action });
  } finally {
    await refreshAll();
  }
}

async function engineSolo(mode) {
  try {
    setOut({ running: true, solo: mode });
    setOut(await api(`/engines/solo/${mode}`, { method: "POST" }));
  } catch (e) {
    setOut({ error: e.message, solo: mode });
  } finally {
    await refreshAll();
  }
}

async function engineLogs(mode) {
  try {
    setOut({ running: true, logs: mode });
    setOut(await api(`/engines/${mode}/logs?lines=160`));
  } catch (e) {
    setOut({ error: e.message, logs: mode });
  } finally {
    await refreshAll();
  }
}

async function doSwitch(mode, selection) {
  if (!selection) throw new Error(`No selection for ${mode}`);
  const bounce = $("chkBounce")?.checked ?? true;
  try {
    setOut({ running: true, action: "switch", mode, selection, bounce });
    setOut(await api("/switch", { method: "POST", body: { mode, model_dir: selection, bounce } }));
  } catch (e) {
    setOut({ error: e.message, action: "switch", mode, selection });
  } finally {
    await refreshAll();
  }
}

function wire() {
  $("btnRefresh").addEventListener("click", refreshAll);

  $("btnTestChat").addEventListener("click", () => runTest("chat"));
  $("btnTestIntent").addEventListener("click", () => runTest("intent"));
  $("btnTestUtil").addEventListener("click", () => runTest("small"));

  // Engine ops
  $("chatStart").addEventListener("click", () => engineAction("chat", "start"));
  $("chatStop").addEventListener("click", () => engineAction("chat", "stop"));
  $("chatRestart").addEventListener("click", () => engineAction("chat", "restart"));
  $("chatSolo").addEventListener("click", () => engineSolo("chat"));
  $("chatLogs").addEventListener("click", () => engineLogs("chat"));
  $("chatTest").addEventListener("click", () => runTest("chat"));

  $("intentStart").addEventListener("click", () => engineAction("intent", "start"));
  $("intentStop").addEventListener("click", () => engineAction("intent", "stop"));
  $("intentRestart").addEventListener("click", () => engineAction("intent", "restart"));
  $("intentSolo").addEventListener("click", () => engineSolo("intent"));
  $("intentLogs").addEventListener("click", () => engineLogs("intent"));
  $("intentTest").addEventListener("click", () => runTest("intent"));

  $("smallStart").addEventListener("click", () => engineAction("small", "start"));
  $("smallStop").addEventListener("click", () => engineAction("small", "stop"));
  $("smallRestart").addEventListener("click", () => engineAction("small", "restart"));
  $("smallSolo").addEventListener("click", () => engineSolo("small"));
  $("smallLogs").addEventListener("click", () => engineLogs("small"));
  $("smallTest").addEventListener("click", () => runTest("small"));

  // Engine card model switching
  $("chatSwitch").addEventListener("click", async () => doSwitch("chat", $("chatModel").value));
  $("intentSwitch").addEventListener("click", async () => doSwitch("intent", $("intentModel").value));
  $("smallSwitch").addEventListener("click", async () => doSwitch("small", $("smallModel").value));

  // Dashboard model switching
  $("btnSwitchChat").addEventListener("click", async () => doSwitch("chat", $("selChat").value));
  $("btnSwitchIntent").addEventListener("click", async () => doSwitch("intent", $("selIntent").value));
  $("btnSwitchSmall").addEventListener("click", async () => doSwitch("small", $("selSmall").value));

  let timer = null;
  $("autoRefresh").addEventListener("change", (e) => {
    if (e.target.checked) timer = setInterval(refreshAll, 5000);
    else if (timer) { clearInterval(timer); timer = null; }
  });
}

wire();
refreshAll();
