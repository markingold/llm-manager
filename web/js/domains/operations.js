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

const UI_BUILD = "phase-a-ops-v5";
const MAX_CONVERSION_ROWS = 30;

let MODEL_CACHE = null;
let ENGINE_STATUS_CACHE = null;

const SLOT_UI = {
  chat: {
    prefix: "chat",
    testButtonId: "chatTest",
    quickTestButtonId: "btnTestChat",
    backendPillId: "chatBackendPill",
    modelPillId: "chatModelPill",
    recommendationPillId: "chatRecPill",
    gateHintId: "chatGateHint",
  },
  intent: {
    prefix: "intent",
    testButtonId: "intentTest",
    quickTestButtonId: "btnTestIntent",
    backendPillId: "intentBackendPill",
    modelPillId: "intentModelPill",
    recommendationPillId: "intentRecPill",
    gateHintId: "intentGateHint",
  },
  small: {
    prefix: "small",
    testButtonId: "smallTest",
    quickTestButtonId: "btnTestUtil",
    backendPillId: "smallBackendPill",
    modelPillId: "smallModelPill",
    recommendationPillId: "smallRecPill",
    gateHintId: "smallGateHint",
  },
};

const BACKEND_CAPABILITIES = {
  tgw: {
    supportsSlotTest: true,
    supportsNoThinking: true,
  },
  vllm: {
    supportsSlotTest: true,
    supportsNoThinking: false,
  },
  tabbyapi: {
    supportsSlotTest: true,
    supportsNoThinking: false,
  },
};


function normalizeBackendName(raw) {
  const name = String(raw || "").trim().toLowerCase();
  return name || "unknown";
}


function setButtonAvailability(buttonId, enabled, reason = "") {
  const button = $(buttonId);
  if (!button) return;
  button.disabled = !enabled;
  button.title = !enabled && reason ? reason : "";
}


function applyOptionalDisable(buttonId, shouldDisable, reason = "") {
  const button = $(buttonId);
  if (!button) return;
  if (shouldDisable) {
    button.disabled = true;
    button.title = reason;
    return;
  }
  if (!button.disabled) {
    button.title = "";
  }
}


function modelBackendCompatibility(backend, meta) {
  if (!meta || typeof meta !== "object") {
    return { compatible: true, reason: "" };
  }

  const recommended = normalizeBackendName(meta.recommended_backend || "");
  const fallbackBackends = Array.isArray(meta.fallback_backends)
    ? meta.fallback_backends.map((item) => normalizeBackendName(item)).filter(Boolean)
    : [];

  if (!recommended || recommended === "unknown") {
    return { compatible: true, reason: "" };
  }

  if (backend === recommended || fallbackBackends.includes(backend)) {
    return { compatible: true, reason: "" };
  }

  const fallbackLabel = fallbackBackends.length ? ` (fallback: ${fallbackBackends.join(", ")})` : "";
  return {
    compatible: false,
    reason: `Active model prefers ${recommended}${fallbackLabel}.`,
  };
}


function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatTs(ts) {
  if (!ts) return "-";
  try {
    const dt = new Date(ts);
    if (Number.isNaN(dt.getTime())) return String(ts);
    return dt.toLocaleString();
  } catch (_) {
    return String(ts);
  }
}

function formatBytes(size) {
  const n = Number(size);
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = n;
  let idx = 0;
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024;
    idx += 1;
  }
  return `${value.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
}

function conversionTone(status) {
  const s = String(status || "").toLowerCase();
  if (s === "completed" || s === "ok" || s === "ready") return "ok";
  if (s === "error" || s === "failed" || s === "missing_output") return "bad";
  return "warn";
}

function setPill(el, text, tone = "warn", title = "") {
  if (!el) return;
  el.textContent = text;
  el.className = `pill ${tone}`;
  el.title = title;
}


function applySlotOperationGating(slotMode, models, engines) {
  const ui = SLOT_UI[slotMode];
  if (!ui) {
    return { slotEnabled: false, canTest: false, supportsNoThinking: false };
  }

  const engine = engines?.[slotMode] || {};
  const backend = normalizeBackendName(engine.backend || models?.slot_backends?.[slotMode]);
  const backendCaps = BACKEND_CAPABILITIES[backend] || null;
  const slotEnabled = Boolean(models?.slots_enabled?.[slotMode]);
  const baseReady = Boolean(String(engine.base || "").trim());

  const activeModelName = basename(models?.active?.[slotMode]);
  const meta = activeModelName ? (models?.meta?.[activeModelName] || null) : null;
  const modelKind = String(meta?.kind || "unknown");
  const recommended = normalizeBackendName(meta?.recommended_backend || "");
  const fallbackBackends = Array.isArray(meta?.fallback_backends)
    ? meta.fallback_backends.map((item) => normalizeBackendName(item)).filter(Boolean)
    : [];
  const compatibility = modelBackendCompatibility(backend, meta);

  const backendTone = backendCaps ? "ok" : "warn";
  const backendSource = String(engine.base_source || "").trim();
  setPill(
    $(ui.backendPillId),
    `backend: ${backend}${backendSource ? ` (${backendSource})` : ""}`,
    backendTone,
    backendCaps ? "" : `Unsupported backend '${backend}' for slot test operations.`
  );

  const modelTone = activeModelName && modelKind !== "unknown" ? "ok" : "warn";
  setPill(
    $(ui.modelPillId),
    `model: ${activeModelName || "(none)"} / ${modelKind}`,
    modelTone,
    activeModelName ? "" : "No active model linked for this slot."
  );

  let recommendationLabel = `recommendation: ${recommended || "n/a"}`;
  if (fallbackBackends.length) {
    recommendationLabel += ` | fallback: ${fallbackBackends.join(",")}`;
  }
  setPill(
    $(ui.recommendationPillId),
    recommendationLabel,
    compatibility.compatible ? "ok" : "warn",
    compatibility.reason
  );

  let canTest = true;
  let gateReason = "";
  if (!slotEnabled) {
    canTest = false;
    gateReason = "Slot disabled by ENABLE_* setting.";
  } else if (!backendCaps) {
    canTest = false;
    gateReason = `Unsupported backend '${backend}' for slot test operations.`;
  } else if (!backendCaps.supportsSlotTest) {
    canTest = false;
    gateReason = `Backend '${backend}' does not support slot test operations.`;
  } else if (!baseReady) {
    canTest = false;
    gateReason = "No API base resolved for this slot.";
  } else if (!compatibility.compatible) {
    canTest = false;
    gateReason = compatibility.reason || "Backend/model capability mismatch.";
  }

  setButtonAvailability(ui.testButtonId, canTest, gateReason);
  setButtonAvailability(ui.quickTestButtonId, canTest, gateReason);

  const canSwitch = slotEnabled;
  setButtonAvailability(`${ui.prefix}Switch`, canSwitch, "Slot disabled by ENABLE_* setting.");

  const disableMutations = !slotEnabled;
  const disableReason = "Slot disabled by ENABLE_* setting.";
  applyOptionalDisable(`${ui.prefix}Start`, disableMutations, disableReason);
  applyOptionalDisable(`${ui.prefix}Stop`, disableMutations, disableReason);
  applyOptionalDisable(`${ui.prefix}Restart`, disableMutations, disableReason);
  applyOptionalDisable(`${ui.prefix}Solo`, disableMutations, disableReason);
  applyOptionalDisable(`${ui.prefix}Logs`, disableMutations, disableReason);

  const hint = $(ui.gateHintId);
  if (hint) {
    hint.textContent = canTest
      ? `ops: test path is enabled for ${backend} with current model compatibility.`
      : `ops: test path disabled — ${gateReason}`;
  }

  return {
    slotEnabled,
    canTest,
    supportsNoThinking: canTest && Boolean(backendCaps?.supportsNoThinking),
  };
}


function applyBackendAwareOperationGating(models, engines) {
  const results = [
    applySlotOperationGating("chat", models, engines),
    applySlotOperationGating("intent", models, engines),
    applySlotOperationGating("small", models, engines),
  ];

  const noThinking = $("noThinking");
  if (!noThinking) return;

  const activeTestSlots = results.filter((row) => row.slotEnabled && row.canTest);
  const noThinkingEnabled = activeTestSlots.length > 0 && activeTestSlots.every((row) => row.supportsNoThinking);
  noThinking.disabled = !noThinkingEnabled;
  if (!noThinkingEnabled) {
    noThinking.checked = false;
    noThinking.title = "No thinking toggle is currently available only on TGW-backed test paths.";
  } else {
    noThinking.title = "";
  }
}

function setConversionDetail(payload) {
  const pre = $("convDetailOut");
  if (!pre) return;
  pre.textContent = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
}

function setConversionRowsError(message) {
  const jobsBody = $("convJobsBody");
  const artifactsBody = $("convArtifactsBody");
  const msg = escapeHtml(message || "Unknown conversion panel error");
  if (jobsBody) {
    jobsBody.innerHTML = `<tr><td colspan="6" class="muted">${msg}</td></tr>`;
  }
  if (artifactsBody) {
    artifactsBody.innerHTML = `<tr><td colspan="6" class="muted">${msg}</td></tr>`;
  }
}

async function conversionJobDetail(jobId) {
  if (!jobId) return;
  try {
    setConversionDetail({ loading: true, run: jobId });
    const detail = await api(`/conversions/exl2/jobs/${encodeURIComponent(jobId)}?tail=180`);
    setConversionDetail(detail);
    setOut(detail);
  } catch (e) {
    setConversionDetail({ error: e.message, run: jobId });
    setOut({ error: e.message, run: jobId });
  }
}

async function conversionArtifactDetail(artifactId) {
  if (!artifactId) return;
  try {
    setConversionDetail({ loading: true, artifact: artifactId });
    const detail = await api(`/conversions/exl2/artifacts/${encodeURIComponent(artifactId)}`);
    setConversionDetail(detail);
    setOut(detail);
  } catch (e) {
    setConversionDetail({ error: e.message, artifact: artifactId });
    setOut({ error: e.message, artifact: artifactId });
  }
}

function renderConversionRows(runs, artifacts) {
  const jobsBody = $("convJobsBody");
  const artifactsBody = $("convArtifactsBody");
  if (jobsBody) {
    if (!runs.length) {
      jobsBody.innerHTML = "<tr><td colspan=\"6\" class=\"muted\">No managed conversion runs found.</td></tr>";
    } else {
      jobsBody.innerHTML = runs.slice(0, MAX_CONVERSION_ROWS).map((row) => {
        const jobId = String(row.job_id || row.id || "");
        const status = String(row.status || "unknown");
        const quant = `b${row.bits ?? "?"} / g${row.groupsize ?? "?"}`;
        const updated = row.updated_ts || row.completed_ts || row.started_ts || row.created_ts;
        const sourceType = String(row.source_type || "huggingface_repo");
        const sourceLabel = sourceType === "merged_local_model"
          ? (row.model_key || row.source_repo_id || "-")
          : (row.source_repo_id || "-");
        const btn = jobId
          ? `<button data-conv-job="${escapeHtml(jobId)}">Details</button>`
          : "<button disabled>Details</button>";
        return `
          <tr>
            <td class="mono">${escapeHtml(jobId || "-")}</td>
            <td>${escapeHtml(sourceType)} / ${escapeHtml(sourceLabel)}</td>
            <td>${escapeHtml(quant)}</td>
            <td><span class="pill ${conversionTone(status)}">${escapeHtml(status)}</span></td>
            <td>${escapeHtml(formatTs(updated))}</td>
            <td>${btn}</td>
          </tr>
        `;
      }).join("");
      jobsBody.querySelectorAll("button[data-conv-job]").forEach((btn) => {
        btn.addEventListener("click", () => conversionJobDetail(btn.getAttribute("data-conv-job") || ""));
      });
    }
  }

  if (artifactsBody) {
    if (!artifacts.length) {
      artifactsBody.innerHTML = "<tr><td colspan=\"6\" class=\"muted\">No managed conversion artifacts found.</td></tr>";
    } else {
      artifactsBody.innerHTML = artifacts.slice(0, MAX_CONVERSION_ROWS).map((row) => {
        const artifactId = String(row.artifact_id || "");
        const status = String(row.status || "unknown");
        const sourceType = String(row.source_type || "huggingface_repo");
        const sourceLabel = sourceType === "merged_local_model"
          ? (row.source_repo_id || "-")
          : (row.source_repo_id || "-");
        const btn = artifactId
          ? `<button data-conv-artifact="${escapeHtml(artifactId)}">Details</button>`
          : "<button disabled>Details</button>";
        return `
          <tr>
            <td class="mono">${escapeHtml(artifactId || "-")}</td>
            <td>${escapeHtml(sourceType)} / ${escapeHtml(sourceLabel)}</td>
            <td class="mono">${escapeHtml(row.model_ref || row.output_model_dir || "-")}</td>
            <td><span class="pill ${conversionTone(status)}">${escapeHtml(status)}</span></td>
            <td>${escapeHtml(formatTs(row.created_ts))}</td>
            <td>${btn}</td>
          </tr>
        `;
      }).join("");
      artifactsBody.querySelectorAll("button[data-conv-artifact]").forEach((btn) => {
        btn.addEventListener("click", () => conversionArtifactDetail(btn.getAttribute("data-conv-artifact") || ""));
      });
    }
  }
}

function renderConversionStats(runs, artifacts) {
  const running = runs.filter((row) => String(row.status || "").toLowerCase() === "running").length;
  const completed = runs.filter((row) => String(row.status || "").toLowerCase() === "completed").length;
  const errors = runs.filter((row) => String(row.status || "").toLowerCase() === "error").length;
  const latest = runs[0];

  setPill(
    $("convSummaryPill"),
    `conversions: ${runs.length} jobs / ${artifacts.length} artifacts`,
    errors > 0 ? "bad" : (running > 0 ? "warn" : "ok"),
    latest ? `latest: ${latest.id || latest.job_id || "unknown"}` : "No conversion runs yet"
  );
  setPill($("convRunningPill"), `running: ${running}`, running > 0 ? "warn" : "ok");
  setPill($("convCompletedPill"), `completed: ${completed}`, completed > 0 ? "ok" : "warn");
  setPill($("convErrorPill"), `error: ${errors}`, errors > 0 ? "bad" : "ok");

  const latestArtifact = artifacts[0];
  const artifactText = latestArtifact
    ? `artifacts: ${artifacts.length} (${formatBytes(latestArtifact.output_size_bytes)})`
    : "artifacts: 0";
  setPill(
    $("convArtifactsPill"),
    artifactText,
    artifacts.length > 0 ? "ok" : "warn",
    latestArtifact ? `latest: ${latestArtifact.artifact_id || "unknown"}` : "No artifacts yet"
  );
}

function buildConversionStartPayload() {
  const sourceType = $("convSourceType")?.value?.trim() || "huggingface_repo";
  const repoId = $("convRepoId")?.value?.trim() || "";
  const modelKey = $("convModelKey")?.value?.trim() || "";
  if (sourceType === "huggingface_repo" && !repoId) {
    throw new Error("Repo ID is required for Hugging Face source.");
  }
  if (sourceType === "merged_local_model" && !modelKey) {
    throw new Error("Model key is required for merged local source.");
  }

  const bits = Number($("convBits")?.value || "");
  if (!Number.isFinite(bits) || bits <= 0) throw new Error("Bits must be a positive number.");

  const groupsize = Number.parseInt($("convGroupsize")?.value || "", 10);
  if (!Number.isFinite(groupsize) || groupsize <= 0) throw new Error("Groupsize must be a positive integer.");

  const payload = {
    source_type: sourceType,
    bits,
    groupsize,
    force: Boolean($("convForce")?.checked),
  };
  if (repoId) payload.repo_id = repoId;
  if (modelKey) payload.model_key = modelKey;

  const sourceModelDir = $("convSourceModelDir")?.value?.trim();
  const outputDir = $("convOutputDir")?.value?.trim();
  const baseModelsDir = $("convBaseModelsDir")?.value?.trim();
  const webuiModelsDir = $("convWebuiModelsDir")?.value?.trim();
  const exllamaRoot = $("convExllamaRoot")?.value?.trim();
  if (sourceModelDir) payload.source_model_dir = sourceModelDir;
  if (outputDir) payload.output_dir = outputDir;
  if (baseModelsDir) payload.base_models_dir = baseModelsDir;
  if (webuiModelsDir) payload.webui_models_dir = webuiModelsDir;
  if (exllamaRoot) payload.exllama_root = exllamaRoot;

  return payload;
}

function applyConversionSourceTypeUI() {
  const sourceType = $("convSourceType")?.value?.trim() || "huggingface_repo";
  const repoInput = $("convRepoId");
  const modelKeyInput = $("convModelKey");
  if (repoInput) repoInput.disabled = sourceType !== "huggingface_repo";
  if (modelKeyInput) modelKeyInput.disabled = sourceType !== "merged_local_model";
}

async function refreshConversionPanel() {
  const hasPanel = Boolean($("convSummaryPill") || $("convJobsBody") || $("convArtifactsBody"));
  if (!hasPanel) return;

  try {
    const [jobsRes, artifactsRes] = await Promise.all([
      api("/conversions/exl2/jobs?limit=200"),
      api("/conversions/exl2/artifacts?limit=200"),
    ]);
    const runs = Array.isArray(jobsRes?.runs) ? jobsRes.runs : [];
    const artifacts = Array.isArray(artifactsRes?.artifacts) ? artifactsRes.artifacts : [];
    renderConversionStats(runs, artifacts);
    renderConversionRows(runs, artifacts);
  } catch (e) {
    setPill($("convSummaryPill"), "conversions: load error", "bad", e.message || "Unknown error");
    setPill($("convRunningPill"), "running: ?", "bad");
    setPill($("convCompletedPill"), "completed: ?", "bad");
    setPill($("convErrorPill"), "error: ?", "bad");
    setPill($("convArtifactsPill"), "artifacts: ?", "bad");
    setConversionRowsError(e.message || "Failed to load conversion metadata");
  }
}

async function startManagedExl2Conversion() {
  const startBtn = $("convStartBtn");
  if (startBtn) startBtn.disabled = true;
  try {
    const payload = buildConversionStartPayload();
    setOut({ running: true, action: "conversions_exl2_start", payload });
    const started = await api("/conversions/exl2", { method: "POST", body: payload });
    setOut(started);
    setConversionDetail(started);
  } catch (e) {
    setOut({ error: e.message, action: "conversions_exl2_start" });
    setConversionDetail({ error: e.message });
  } finally {
    if (startBtn) startBtn.disabled = false;
    await refreshConversionPanel();
  }
}


function renderTgwWebUiControls(tgw) {
  const stateEl = $("tgwWebuiState");
  const metaEl = $("tgwWebuiMeta");
  const openBtn = $("tgwWebuiOpen");
  const startBtn = $("tgwWebuiStart");
  const stopBtn = $("tgwWebuiStop");
  const restartBtn = $("tgwWebuiRestart");
  const logsBtn = $("tgwWebuiLogs");
  if (!stateEl || !metaEl || !openBtn || !startBtn || !stopBtn || !restartBtn || !logsBtn) return;

  if (!tgw) {
    stateEl.textContent = "TGW UI UNKNOWN";
    stateEl.className = "pill bad";
    metaEl.textContent = "TGW WebUI status unavailable.";
    startBtn.disabled = true;
    stopBtn.disabled = true;
    restartBtn.disabled = true;
    logsBtn.disabled = true;
    openBtn.disabled = true;
    return;
  }

  const active = tgw.active || tgw.active_state === "active";
  const tone = active ? (tgw.listening ? "ok" : "warn") : "warn";
  let label = "TGW UI STOPPED";
  if (active && tgw.listening) label = "TGW UI ON";
  else if (active) label = "TGW UI STARTING";

  stateEl.textContent = label;
  stateEl.className = `pill ${tone}`;

  const launchUrl = tgw.launch_url || "(no launch URL)";
  const unit = tgw.unit || "(unit unknown)";
  metaEl.textContent = `${unit} | bind=${tgw.bind_host}:${tgw.port} | launch=${launchUrl}`;

  startBtn.disabled = false;
  stopBtn.disabled = false;
  restartBtn.disabled = false;
  logsBtn.disabled = false;
  openBtn.disabled = !tgw.launch_url;
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
    renderTgwWebUiControls(s.tgw_webui);

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
    applyBackendAwareOperationGating(MODEL_CACHE, ENGINE_STATUS_CACHE);

    try {
      const v = await api("/vram");
      const vramEl = $("vramInfo");
      if (vramEl && v.gpus) {
        vramEl.textContent = v.gpus.map(g =>
          `GPU ${g.index}: ${g.name}  ${g.vram_used_mb}/${g.vram_total_mb} MB  (${g.gpu_util_pct}%)`
        ).join("  |  ");
      }
    } catch (_) { }

    await Promise.all([refreshEvalPanel(), refreshRouterPanel(), refreshConversionPanel()]);
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


async function tgwWebUiAction(action) {
  try {
    setOut({ running: true, mode: "tgw-webui", action });
    setOut(await api(`/engines/tgw-webui/${action}`, { method: "POST" }));
  } catch (e) {
    setOut({ error: e.message, mode: "tgw-webui", action });
  } finally {
    await refreshAll();
  }
}


async function tgwWebUiLogs() {
  try {
    setOut({ running: true, mode: "tgw-webui", action: "logs" });
    setOut(await api("/engines/tgw-webui/logs?lines=160"));
  } catch (e) {
    setOut({ error: e.message, mode: "tgw-webui", action: "logs" });
  } finally {
    await refreshAll();
  }
}


function openTgwWebUi() {
  const tgw = ENGINE_STATUS_CACHE?.tgw_webui;
  if (!tgw?.launch_url) {
    setOut({ error: "TGW WebUI launch URL is unavailable." });
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
  const bounce = true;
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
  $("convStartBtn")?.addEventListener("click", startManagedExl2Conversion);
  $("convRefreshBtn")?.addEventListener("click", refreshConversionPanel);
  $("convSourceType")?.addEventListener("change", applyConversionSourceTypeUI);
  $("convRepoId")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      startManagedExl2Conversion();
    }
  });
  $("convModelKey")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      startManagedExl2Conversion();
    }
  });

  applyConversionSourceTypeUI();

  $("btnTestChat")?.addEventListener("click", () => runTest("chat"));
  $("btnTestIntent")?.addEventListener("click", () => runTest("intent"));
  $("btnTestUtil")?.addEventListener("click", () => runTest("small"));

  $("chatStart")?.addEventListener("click", () => engineAction("chat", "start"));
  $("chatStop")?.addEventListener("click", () => engineAction("chat", "stop"));
  $("chatRestart")?.addEventListener("click", () => engineAction("chat", "restart"));
  $("chatSolo")?.addEventListener("click", () => engineSolo("chat"));
  $("chatLogs")?.addEventListener("click", () => engineLogs("chat"));
  $("chatTest")?.addEventListener("click", () => runTest("chat"));
  $("tgwWebuiStart")?.addEventListener("click", () => tgwWebUiAction("start"));
  $("tgwWebuiStop")?.addEventListener("click", () => tgwWebUiAction("stop"));
  $("tgwWebuiRestart")?.addEventListener("click", () => tgwWebUiAction("restart"));
  $("tgwWebuiLogs")?.addEventListener("click", tgwWebUiLogs);
  $("tgwWebuiOpen")?.addEventListener("click", openTgwWebUi);

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

  let timer = null;
  $("autoRefresh")?.addEventListener("change", (e) => {
    if (e.target.checked) timer = setInterval(refreshAll, 5000);
    else if (timer) {
      clearInterval(timer);
      timer = null;
    }
  });
}
