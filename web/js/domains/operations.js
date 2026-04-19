import { api } from "../api.js";
import {
  $,
  setStatus,
  setOut,
  basename,
  engineTone,
  engineLabel,
  fmtEngineMeta,
  setSelectOptions,
  setEngineButtonStates,
} from "../ui-core.js";
import { refreshEvalPanel } from "./evaluation.js";
import { refreshRouterPanel } from "./router.js";

const UI_BUILD = "phase-a-ops-v2";

let MODEL_CACHE = null;
let ENGINE_STATUS_CACHE = null;


function renderChatTgwControls(chatStatus) {
  const tgw = chatStatus?.tgw_webui;
  const stateEl = $("chatTgwState");
  const metaEl = $("chatTgwMeta");
  const toggleBtn = $("chatTgwToggle");
  const openBtn = $("chatTgwOpen");
  if (!stateEl || !metaEl || !toggleBtn || !openBtn) return;

  if (!tgw) {
    stateEl.textContent = "TGW UI UNKNOWN";
    stateEl.className = "pill bad";
    metaEl.textContent = "TGW WebUI status unavailable.";
    toggleBtn.disabled = true;
    openBtn.disabled = true;
    return;
  }

  if (!tgw.available) {
    stateEl.textContent = "TGW UI UNAVAILABLE";
    stateEl.className = "pill bad";
    metaEl.textContent = tgw.detail || "TGW WebUI is only available when chat uses the tgw backend.";
    toggleBtn.disabled = true;
    toggleBtn.textContent = "TGW UI unavailable";
    openBtn.disabled = true;
    return;
  }

  const chatActive = chatStatus?.systemd?.ActiveState === "active";
  const tone = tgw.enabled ? (tgw.listening ? "ok" : "warn") : "warn";
  let label = "TGW UI OFF";
  if (tgw.enabled && tgw.listening) label = "TGW UI ON";
  else if (tgw.enabled && chatActive) label = "TGW UI STARTING";
  else if (tgw.enabled) label = "TGW UI ARMED";

  stateEl.textContent = label;
  stateEl.className = `pill ${tone}`;

  const launchUrl = tgw.launch_url || "(no launch URL)";
  metaEl.textContent = `bind=${tgw.bind_host}:${tgw.port} | launch=${launchUrl}`;

  toggleBtn.disabled = false;
  toggleBtn.textContent = tgw.enabled ? "Disable TGW UI" : "Enable TGW UI";
  openBtn.disabled = !tgw.effective_enabled || !tgw.launch_url;
}

async function refreshModels() {
  const m = await api("/models");
  MODEL_CACHE = m;

  const activeChat = basename(m.active?.chat);
  const activeIntent = basename(m.active?.intent);
  const activeSmall = basename(m.active?.small);

  const slots = m.slots_enabled || { chat: true, intent: true, small: true };
  const toggleSlot = (id, visible) => {
    const el = $(id);
    if (el) el.style.display = visible ? "" : "none";
  };
  toggleSlot("engineChat", slots.chat);
  toggleSlot("engineIntent", slots.intent);
  toggleSlot("engineSmall", slots.small);

  setSelectOptions($("selChat"), m.chat || [], activeChat);
  setSelectOptions($("selIntent"), m.intent || [], activeIntent);
  setSelectOptions($("selSmall"), m.small || [], activeSmall);

  setSelectOptions($("chatModel"), m.chat || [], activeChat);
  setSelectOptions($("intentModel"), m.intent || [], activeIntent);
  setSelectOptions($("smallModel"), m.small || [], activeSmall);
}

export async function refreshAll() {
  try {
    const hint = $("cacheHint");
    if (hint) {
      hint.textContent = `JS: ${UI_BUILD}`;
      hint.className = "pill ok";
      hint.title = "This proves the newest app.js loaded (cache-bust working).";
    }

    const h = await api("/health");
    setStatus(`OK - ${h.time}`, "ok");

    await refreshModels();

    const s = await api("/engines/status");
    ENGINE_STATUS_CACHE = s;

    $("chatMeta").textContent = fmtEngineMeta(s.chat);
    $("intentMeta").textContent = fmtEngineMeta(s.intent);
    $("smallMeta").textContent = fmtEngineMeta(s.small);
    renderChatTgwControls(s.chat);

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

    setEngineButtonStates("chat", s.chat.systemd?.ActiveState);
    setEngineButtonStates("intent", s.intent.systemd?.ActiveState);
    setEngineButtonStates("small", s.small.systemd?.ActiveState);

    try {
      const v = await api("/vram");
      const vramEl = $("vramInfo");
      if (vramEl && v.gpus) {
        vramEl.textContent = v.gpus.map(g =>
          `GPU ${g.index}: ${g.name}  ${g.vram_used_mb}/${g.vram_total_mb} MB  (${g.gpu_util_pct}%)`
        ).join("  |  ");
      }
    } catch (_) { }

    await Promise.all([refreshEvalPanel(), refreshRouterPanel()]);
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


async function toggleChatTgwWebUi() {
  const tgw = ENGINE_STATUS_CACHE?.chat?.tgw_webui;
  if (!tgw) {
    setOut({ error: "TGW WebUI status is unavailable for chat." });
    return;
  }
  try {
    const enabled = !tgw.enabled;
    setOut({ running: true, mode: "chat", action: enabled ? "enable_tgw_webui" : "disable_tgw_webui" });
    setOut(await api("/engines/chat/tgw-webui", { method: "POST", body: { enabled, restart: true } }));
  } catch (e) {
    setOut({ error: e.message, mode: "chat", action: "toggle_tgw_webui" });
  } finally {
    await refreshAll();
  }
}


function openChatTgwWebUi() {
  const tgw = ENGINE_STATUS_CACHE?.chat?.tgw_webui;
  if (!tgw?.effective_enabled || !tgw.launch_url) {
    setOut({ error: "TGW WebUI is not enabled for chat, or no launch URL is available." });
    return;
  }
  window.open(tgw.launch_url, "_blank", "noopener,noreferrer");
}

async function runTest(mode) {
  const q = $("q").value.trim() || "Hello";
  const nt = $("noThinking").checked ? "&no_thinking=1" : "";
  const path = mode === "chat" ? `/test-chat?q=${encodeURIComponent(q)}${nt}`
    : mode === "intent" ? `/test-intent?q=${encodeURIComponent(q)}${nt}`
      : `/test-util?q=${encodeURIComponent(q)}${nt}`;
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

export function wireOperationsDomain() {
  $("btnRefresh")?.addEventListener("click", refreshAll);

  $("btnTestChat")?.addEventListener("click", () => runTest("chat"));
  $("btnTestIntent")?.addEventListener("click", () => runTest("intent"));
  $("btnTestUtil")?.addEventListener("click", () => runTest("small"));

  $("chatStart")?.addEventListener("click", () => engineAction("chat", "start"));
  $("chatStop")?.addEventListener("click", () => engineAction("chat", "stop"));
  $("chatRestart")?.addEventListener("click", () => engineAction("chat", "restart"));
  $("chatSolo")?.addEventListener("click", () => engineSolo("chat"));
  $("chatLogs")?.addEventListener("click", () => engineLogs("chat"));
  $("chatTest")?.addEventListener("click", () => runTest("chat"));
  $("chatTgwToggle")?.addEventListener("click", toggleChatTgwWebUi);
  $("chatTgwOpen")?.addEventListener("click", openChatTgwWebUi);

  $("intentStart")?.addEventListener("click", () => engineAction("intent", "start"));
  $("intentStop")?.addEventListener("click", () => engineAction("intent", "stop"));
  $("intentRestart")?.addEventListener("click", () => engineAction("intent", "restart"));
  $("intentSolo")?.addEventListener("click", () => engineSolo("intent"));
  $("intentLogs")?.addEventListener("click", () => engineLogs("intent"));
  $("intentTest")?.addEventListener("click", () => runTest("intent"));

  $("smallStart")?.addEventListener("click", () => engineAction("small", "start"));
  $("smallStop")?.addEventListener("click", () => engineAction("small", "stop"));
  $("smallRestart")?.addEventListener("click", () => engineAction("small", "restart"));
  $("smallSolo")?.addEventListener("click", () => engineSolo("small"));
  $("smallLogs")?.addEventListener("click", () => engineLogs("small"));
  $("smallTest")?.addEventListener("click", () => runTest("small"));

  $("chatSwitch")?.addEventListener("click", async () => doSwitch("chat", $("chatModel").value));
  $("intentSwitch")?.addEventListener("click", async () => doSwitch("intent", $("intentModel").value));
  $("smallSwitch")?.addEventListener("click", async () => doSwitch("small", $("smallModel").value));

  $("btnSwitchChat")?.addEventListener("click", async () => doSwitch("chat", $("selChat").value));
  $("btnSwitchIntent")?.addEventListener("click", async () => doSwitch("intent", $("selIntent").value));
  $("btnSwitchSmall")?.addEventListener("click", async () => doSwitch("small", $("selSmall").value));

  let timer = null;
  $("autoRefresh")?.addEventListener("change", (e) => {
    if (e.target.checked) timer = setInterval(refreshAll, 5000);
    else if (timer) {
      clearInterval(timer);
      timer = null;
    }
  });
}
