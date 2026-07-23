import { api } from "../api.js?v=20260722_3";
import { $, escapeHtml, setOut } from "../ui-core.js?v=20260722_3";

const API_FILTER_KEYS = [
  "status",
  "target_mode",
  "project",
  "provider",
  "lane",
  "model",
  "tag",
  "since_ts",
  "suite_pass",
  "limit",
];

const URL_FILTER_KEYS = [...API_FILTER_KEYS, "preset", "run_id", "suite_name", "suite_version"];

const DEFAULT_FILTERS = {
  status: "completed",
  target_mode: "",
  project: "",
  provider: "",
  lane: "",
  model: "",
  tag: "",
  since_ts: "",
  suite_pass: "",
  limit: "25",
  preset: "",
  run_id: "",
  suite_name: "",
  suite_version: "1",
};

const PRESETS = {
  failures_recent: { status: "completed", suite_pass: "false", limit: "50" },
  infra_errors: { status: "error", suite_pass: "", limit: "50" },
  intent_regressions: { status: "completed", target_mode: "intent", suite_pass: "false", limit: "50" },
  openrouter_fallbacks: { status: "completed", provider: "openrouter", suite_pass: "false", limit: "50" },
  queue_watch: { status: "queued", suite_pass: "", limit: "100" },
};

let LAST_QUEUE = null;
let LAST_WORKERS = null;

function urlParams() {
  return new URLSearchParams(window.location.search || "");
}

function loadFiltersFromUrl() {
  const params = urlParams();
  const result = { ...DEFAULT_FILTERS };
  for (const key of URL_FILTER_KEYS) {
    const raw = params.get(`er_${key}`);
    if (raw !== null) result[key] = raw;
  }
  return result;
}

function persistFiltersToUrl(filters) {
  const params = urlParams();
  for (const key of URL_FILTER_KEYS) {
    const value = String(filters[key] ?? "").trim();
    if (!value || value === String(DEFAULT_FILTERS[key] ?? "")) params.delete(`er_${key}`);
    else params.set(`er_${key}`, value);
  }
  const query = params.toString();
  window.history.replaceState({}, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
}

function getFilterInputValues() {
  return {
    status: $("evalFilterStatus")?.value || DEFAULT_FILTERS.status,
    target_mode: $("evalFilterMode")?.value || "",
    project: $("evalFilterProject")?.value?.trim() || "",
    provider: $("evalFilterProvider")?.value?.trim() || "",
    lane: $("evalFilterLane")?.value?.trim() || "",
    model: $("evalFilterModel")?.value?.trim() || "",
    tag: $("evalFilterTag")?.value?.trim() || "",
    since_ts: $("evalFilterSince")?.value?.trim() || "",
    suite_pass: $("evalFilterPass")?.value || "",
    limit: $("evalFilterLimit")?.value || DEFAULT_FILTERS.limit,
    preset: $("evalFilterPreset")?.value || "",
    run_id: $("evalRunDrawer")?.dataset?.runId || "",
    suite_name: $("suiteName")?.value?.trim() || "",
    suite_version: $("suiteVersion")?.value?.trim() || "1",
  };
}

function applyFilterValuesToInputs(filters) {
  if ($("evalFilterStatus")) $("evalFilterStatus").value = filters.status || DEFAULT_FILTERS.status;
  if ($("evalFilterMode")) $("evalFilterMode").value = filters.target_mode || "";
  if ($("evalFilterProject")) $("evalFilterProject").value = filters.project || "";
  if ($("evalFilterProvider")) $("evalFilterProvider").value = filters.provider || "";
  if ($("evalFilterLane")) $("evalFilterLane").value = filters.lane || "";
  if ($("evalFilterModel")) $("evalFilterModel").value = filters.model || "";
  if ($("evalFilterTag")) $("evalFilterTag").value = filters.tag || "";
  if ($("evalFilterSince")) $("evalFilterSince").value = filters.since_ts || "";
  if ($("evalFilterPass")) $("evalFilterPass").value = filters.suite_pass || "";
  if ($("evalFilterLimit")) $("evalFilterLimit").value = filters.limit || DEFAULT_FILTERS.limit;
  if ($("evalFilterPreset")) $("evalFilterPreset").value = filters.preset || "";
  if ($("suiteName")) $("suiteName").value = filters.suite_name || "";
  if ($("suiteVersion")) $("suiteVersion").value = filters.suite_version || "1";
}

function shortRunId(runId) {
  if (!runId) return "";
  return runId.length > 10 ? `${runId.slice(0, 10)}...` : runId;
}

function runPassPill(run) {
  if (run?.suite_pass === true) return '<span class="pill ok">pass</span>';
  if (run?.suite_pass === false) return '<span class="pill bad">fail</span>';
  return '<span class="pill">n/a</span>';
}

function fmtPct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return `${(n * 100).toFixed(1)}%`;
}

function fmtUsd(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  if (n === 0) return "$0";
  if (n < 0.000001) return `$${n.toExponential(2)}`;
  if (n < 0.01) return `$${n.toFixed(6)}`;
  return `$${n.toFixed(4)}`;
}

function laneLabel(row) {
  if (!row || typeof row !== "object") return "-";
  return `${row.provider || "-"}/${row.lane || "-"}`;
}

function savingsText(group) {
  if (!group || typeof group !== "object") return "-";
  if (group.decision === "reference_is_cheapest_sufficient") return "already cheapest";
  const s = group.savings;
  if (s && typeof s === "object") {
    const pct = Number(s.avg_cost_reduction_pct);
    const usd = Number(s.avg_cost_reduction_usd);
    if (Number.isFinite(pct) || Number.isFinite(usd)) {
      const pctText = Number.isFinite(pct) ? `${(pct * 100).toFixed(1)}%` : "-";
      const usdText = Number.isFinite(usd) ? fmtUsd(usd) : "-";
      return `${pctText} (${usdText})`;
    }
  }
  if (group.decision === "cheaper_lane_sufficient") return "cheaper lane sufficient";
  return "none";
}

function updateEvalContext() {
  const q = LAST_QUEUE || {};
  const w = LAST_WORKERS || {};
  const caps = w.priority_running_caps || q.priority_running_caps || {};
  if ($("evalQueueCtx")) {
    $("evalQueueCtx").textContent = `queue q=${q.queued_count || 0} r=${q.running_count || 0} done=${q.completed_count || 0} err=${q.error_count || 0}`;
  }
  if ($("evalWorkerCtx")) {
    $("evalWorkerCtx").textContent = `workers ${w.worker_count || q.worker_count || 0} tick=${w.worker_tick_seconds || "-"}s`;
  }
  if ($("evalCapsCtx")) {
    $("evalCapsCtx").textContent = `caps i=${caps.interactive ?? "-"} b=${caps.batch ?? "-"} e=${caps.evaluation ?? "-"}`;
  }
}

function defaultSuiteTemplate(name, version) {
  return {
    target_mode: "chat",
    candidate_models: ["chat_active_model"],
    variants: [{ variant_id: "baseline", temperature: 0.2, top_p: 0.9, max_tokens: 120, stop: [] }],
    cases: [{ case_id: "case-1", prompt: "Hello", expected_contains: ["Hello"], tags: ["smoke"] }],
    case_pass_threshold_pct: 1.0,
    suite_pass_threshold_pct: 1.0,
    metadata: { project: "llm-manager", owner: "ops", suite_name: name, suite_version: version },
  };
}

function parseSuiteEditor() {
  const raw = $("suiteEditor")?.value?.trim() || "";
  if (!raw) throw new Error("Suite editor JSON is empty");
  const parsed = JSON.parse(raw);
  if (!parsed || typeof parsed !== "object") throw new Error("Suite JSON must be an object");
  return parsed;
}

function setSuiteEditor(payload) {
  if ($("suiteEditor")) {
    $("suiteEditor").value = JSON.stringify(payload || {}, null, 2);
  }
}

async function openEvalReport(runId) {
  if (!runId) return;
  try {
    setOut({ running: true, action: "eval_report", run_id: runId });
    setOut(await api(`/router/evaluations/${encodeURIComponent(runId)}/report`));
  } catch (e) {
    setOut({ error: e.message, action: "eval_report", run_id: runId });
  }
}

async function openEvalRunRaw(runId) {
  if (!runId) return;
  try {
    setOut({ running: true, action: "eval_raw", run_id: runId });
    setOut(await api(`/router/evaluations/${encodeURIComponent(runId)}`));
  } catch (e) {
    setOut({ error: e.message, action: "eval_raw", run_id: runId });
  }
}

function clearDrawerCompareRows(message = "Click Compare to load side-by-side outputs.") {
  const body = $("evalDrawerCompareBody");
  if (body) {
    body.innerHTML = "";
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 4;
    td.className = "muted";
    td.textContent = message;
    tr.appendChild(td);
    body.appendChild(tr);
  }
  if ($("evalDrawerCompareMeta")) $("evalDrawerCompareMeta").textContent = message;
}

function renderDrawerCompareRows(compareRows) {
  const body = $("evalDrawerCompareBody");
  if (!body) return;
  body.innerHTML = "";
  const rows = Array.isArray(compareRows) ? compareRows : [];
  if (!rows.length) return clearDrawerCompareRows("No compare rows available for this run.");

  for (const row of rows) {
    const tr = document.createElement("tr");
    const c1 = document.createElement("td");
    c1.textContent = String(row?.case_id || "-");
    tr.appendChild(c1);

    const c2 = document.createElement("td");
    c2.textContent = String(row?.variant_id || "-");
    tr.appendChild(c2);

    const c3 = document.createElement("td");
    const expected = Array.isArray(row?.expected_contains) ? row.expected_contains : [];
    c3.textContent = expected.length ? expected.join(" | ") : "-";
    tr.appendChild(c3);

    const c4 = document.createElement("td");
    const grid = document.createElement("div");
    grid.className = "compare-grid";
    const outs = Array.isArray(row?.outputs) ? row.outputs : [];
    if (!outs.length) {
      const empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = "No outputs";
      c4.appendChild(empty);
    } else {
      for (const out of outs) {
        const card = document.createElement("div");
        card.className = "compare-cell";
        const meta = document.createElement("div");
        meta.className = "compare-meta";
        meta.textContent = `${out?.provider || "-"} / ${out?.lane || "-"} / ${out?.model || "-"} | ok=${String(out?.ok)}`;
        card.appendChild(meta);

        const text = document.createElement("div");
        text.className = "compare-out mono";
        const normalized = String(out?.output_text || "").replace(/\s+/g, " ").trim();
        text.textContent = normalized.length > 400 ? `${normalized.slice(0, 400)}...` : normalized || "-";
        card.appendChild(text);

        if (out?.error) {
          const err = document.createElement("div");
          err.className = "compare-meta";
          err.textContent = `error=${typeof out.error === "string" ? out.error : JSON.stringify(out.error)}`;
          card.appendChild(err);
        }
        grid.appendChild(card);
      }
      c4.appendChild(grid);
    }
    tr.appendChild(c4);
    body.appendChild(tr);
  }
}

function renderDrawerFailedRows(results) {
  const body = $("evalDrawerFailedBody");
  if (!body) return;
  body.innerHTML = "";
  const rows = (results || []).filter((r) => r && typeof r === "object" && (r.case_pass === false || r.ok === false || !!r.error));
  if (!rows.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="6" class="muted">No failed or risky rows for this run.</td>';
    body.appendChild(tr);
    return;
  }

  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(String(r.case_id || "-"))}</td>
      <td>${escapeHtml(String(r.variant_id || "-"))}</td>
      <td>${escapeHtml(r.provider || "-")} / ${escapeHtml(r.lane || "-")} / ${escapeHtml(r.model || "-")}</td>
      <td>case_pass=${String(r.case_pass)} ok=${String(r.ok)}</td>
      <td class="mono">${escapeHtml((String(r.output_text || "").replace(/\s+/g, " ").trim() || "-").slice(0, 180))}</td>
      <td class="mono">${escapeHtml(r.error ? (typeof r.error === "string" ? r.error : JSON.stringify(r.error)) : "-")}</td>
    `;
    body.appendChild(tr);
  }
}

function renderDrawerRecommendations(recommendations) {
  const list = $("evalDrawerRecommendations");
  if (!list) return;
  list.innerHTML = "";
  const recs = Array.isArray(recommendations) ? recommendations : [];
  if (!recs.length) {
    const li = document.createElement("li");
    li.textContent = "No recommendations recorded.";
    list.appendChild(li);
    return;
  }
  for (const rec of recs) {
    const li = document.createElement("li");
    li.textContent = typeof rec === "string" ? rec : JSON.stringify(rec);
    list.appendChild(li);
  }
}

function setDrawerOpen(open) {
  const drawer = $("evalRunDrawer");
  const backdrop = $("evalDrawerBackdrop");
  if (!drawer || !backdrop) return;
  drawer.style.display = open ? "grid" : "none";
  backdrop.style.display = open ? "" : "none";
}

export function closeEvalRunDetail() {
  const drawer = $("evalRunDrawer");
  if (drawer) drawer.dataset.runId = "";
  setDrawerOpen(false);
  clearDrawerCompareRows();
  const filters = getFilterInputValues();
  filters.run_id = "";
  persistFiltersToUrl(filters);
}

export async function openEvalCompare(runId) {
  if (!runId) return;
  try {
    setOut({ running: true, action: "eval_compare", run_id: runId });
    if ($("evalRunDrawer")?.style.display === "none") await openEvalRunDetail(runId);
    const compact = await api(`/router/evaluations/${encodeURIComponent(runId)}/compare-compact`);
    renderDrawerCompareRows(compact?.compare_rows || []);
    if ($("evalDrawerCompareMeta")) {
      $("evalDrawerCompareMeta").textContent = `Loaded ${Array.isArray(compact?.compare_rows) ? compact.compare_rows.length : 0} compare row(s).`;
    }
    setOut(compact);
  } catch (e) {
    setOut({ error: e.message, action: "eval_compare", run_id: runId });
    clearDrawerCompareRows(`Compare load failed: ${e.message}`);
  }
}

export async function openEvalRunDetail(runId) {
  if (!runId) return;
  const drawer = $("evalRunDrawer");
  if (!drawer) return;
  drawer.dataset.runId = runId;
  setDrawerOpen(true);

  if ($("evalDrawerReport")) $("evalDrawerReport").onclick = () => openEvalReport(runId);
  if ($("btnEvalDrawerReport")) $("btnEvalDrawerReport").onclick = () => openEvalReport(runId);
  if ($("btnEvalDrawerCompare")) $("btnEvalDrawerCompare").onclick = () => openEvalCompare(runId);
  if ($("btnEvalDrawerRaw")) $("btnEvalDrawerRaw").onclick = () => openEvalRunRaw(runId);

  if ($("evalDrawerTitle")) $("evalDrawerTitle").textContent = `Run ${shortRunId(runId)}`;
  if ($("evalDrawerMeta")) $("evalDrawerMeta").textContent = "Loading...";
  if ($("evalDrawerSummary")) $("evalDrawerSummary").textContent = "{}";
  clearDrawerCompareRows();
  renderDrawerRecommendations([]);
  renderDrawerFailedRows([]);

  try {
    const run = await api(`/router/evaluations/${encodeURIComponent(runId)}`);
    const suite = `${run.suite_name || "suite"}#${run.suite_version || "1"}`;
    if ($("evalDrawerTitle")) $("evalDrawerTitle").textContent = `${suite} ${shortRunId(run.run_id || runId)}`;
    const workerHint = LAST_WORKERS ? ` | workers=${LAST_WORKERS.worker_count || 0}` : "";
    if ($("evalDrawerMeta")) {
      $("evalDrawerMeta").textContent = [
        `status=${run.status || "unknown"}`,
        `target=${run.target_mode || "-"}`,
        `created=${run.created_ts || "-"}`,
        `suite_pass=${String(run.suite_pass)}`,
      ].join(" | ") + workerHint;
    }
    if ($("evalDrawerSummary")) $("evalDrawerSummary").textContent = JSON.stringify(run.summary || {}, null, 2);
    renderDrawerRecommendations(run.recommendations || []);
    renderDrawerFailedRows(run.results || []);

    const filters = getFilterInputValues();
    filters.run_id = runId;
    persistFiltersToUrl(filters);
  } catch (e) {
    if ($("evalDrawerMeta")) $("evalDrawerMeta").textContent = `Error loading run: ${e.message}`;
    if ($("evalDrawerSummary")) $("evalDrawerSummary").textContent = JSON.stringify({ error: e.message }, null, 2);
    clearDrawerCompareRows(`Run detail load failed: ${e.message}`);
  }
}

function renderEvalRunActions(runs) {
  const host = $("evalRunActions");
  if (!host) return;
  host.innerHTML = "";
  if (!runs?.length) {
    const empty = document.createElement("span");
    empty.className = "muted";
    empty.textContent = "No recent completed runs.";
    host.appendChild(empty);
    return;
  }
  for (const run of runs) {
    const row = document.createElement("div");
    row.className = "row";
    const rid = run.run_id || "";

    const label = document.createElement("span");
    label.className = "pill";
    label.textContent = `${run.suite_name || "suite"}#${run.suite_version || "1"} ${rid.slice(0, 8)} (${run.suite_pass === true ? "pass" : run.suite_pass === false ? "fail" : "n/a"})`;
    row.appendChild(label);

    const addBtn = (text, fn) => {
      const b = document.createElement("button");
      b.textContent = text;
      b.addEventListener("click", () => fn(rid));
      row.appendChild(b);
    };

    addBtn("Details", openEvalRunDetail);
    addBtn("Report", openEvalReport);
    addBtn("Compare", openEvalCompare);
    addBtn("Raw", openEvalRunRaw);
    host.appendChild(row);
  }
}

function renderRunsTable(runs) {
  const body = $("evalRunsBody");
  const count = $("evalRunsCount");
  if (!body) return;
  body.innerHTML = "";
  const rows = runs || [];
  if (count) count.textContent = `${rows.length} run(s)`;

  if (!rows.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="8" class="muted">No runs matched current filters.</td>';
    body.appendChild(tr);
    return;
  }

  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono" title="${escapeHtml(r.run_id || "")}">${escapeHtml(shortRunId(r.run_id || ""))}</td>
      <td>${escapeHtml(r.suite_name || "-")}</td>
      <td>${escapeHtml(r.suite_version || "-")}</td>
      <td>${escapeHtml(r.target_mode || "-")}</td>
      <td>${escapeHtml(r.status || "-")}</td>
      <td>${runPassPill(r)}</td>
      <td class="mono">${escapeHtml(r.created_ts || "-")}</td>
      <td class="row"></td>
    `;

    const actions = tr.querySelector("td:last-child");
    const addBtn = (label, fn) => {
      const btn = document.createElement("button");
      btn.textContent = label;
      btn.addEventListener("click", () => fn(r.run_id));
      actions.appendChild(btn);
    };

    addBtn("Details", openEvalRunDetail);
    addBtn("Report", openEvalReport);
    addBtn("Compare", openEvalCompare);
    addBtn("Raw", openEvalRunRaw);
    body.appendChild(tr);
  }
}

function buildRunsQuery(filters) {
  const qp = new URLSearchParams();
  for (const key of API_FILTER_KEYS) {
    const value = String(filters[key] ?? "").trim();
    if (!value) continue;
    if (key === "suite_pass") qp.set(key, value === "true" ? "true" : "false");
    else qp.set(key, value);
  }
  return qp.toString();
}

function buildLaneSufficiencyQuery() {
  const qp = new URLSearchParams();
  const targetMode = $("evalLaneTargetMode")?.value?.trim() || "";
  const project = $("evalLaneProject")?.value?.trim() || "";
  const sinceTs = $("evalLaneSince")?.value?.trim() || "";
  const limitGroups = $("evalLaneLimitGroups")?.value || "20";
  const minRows = $("evalLaneMinRows")?.value || "8";
  const minPass = $("evalLaneMinPassRate")?.value || "0.9";
  const maxGap = $("evalLaneMaxPassGap")?.value || "0.05";

  qp.set("limit_groups", limitGroups);
  qp.set("limit_runs", "500");
  qp.set("min_rows_per_lane", minRows);
  qp.set("min_pass_rate", minPass);
  qp.set("max_pass_gap", maxGap);
  if (targetMode) qp.set("target_mode", targetMode);
  if (project) qp.set("project", project);
  if (sinceTs) qp.set("since_ts", sinceTs);
  return qp.toString();
}

function renderLaneSufficiencyRows(groups) {
  const body = $("evalLaneBody");
  if (!body) return;
  body.innerHTML = "";

  const rows = Array.isArray(groups) ? groups : [];
  if (!rows.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="7" class="muted">No lane sufficiency groups matched current filters.</td>';
    body.appendChild(tr);
    return;
  }

  for (const g of rows) {
    const ref = g?.reference_lane || {};
    const cheap = g?.cheapest_sufficient_lane || null;
    const tr = document.createElement("tr");
    const taskModes = Array.isArray(g?.target_modes) && g.target_modes.length ? g.target_modes.join(",") : "-";
    const passRateText = cheap
      ? `ref ${fmtPct(ref.pass_rate)} | cheap ${fmtPct(cheap.pass_rate)}`
      : `ref ${fmtPct(ref.pass_rate)}`;
    const costText = cheap
      ? `ref ${fmtUsd(ref.avg_estimated_cost_usd)} | cheap ${fmtUsd(cheap.avg_estimated_cost_usd)}`
      : `ref ${fmtUsd(ref.avg_estimated_cost_usd)}`;
    tr.innerHTML = `
      <td>${escapeHtml(g.task_group || "-")}<div class="muted">modes:${escapeHtml(taskModes)}</div></td>
      <td>${escapeHtml(laneLabel(ref))}</td>
      <td>${escapeHtml(cheap ? laneLabel(cheap) : "-")}</td>
      <td>${passRateText}</td>
      <td>${costText}</td>
      <td>${savingsText(g)}</td>
      <td>${g.run_count || 0}</td>
    `;
    body.appendChild(tr);
  }
}

function setLaneSufficiencyPills(report) {
  const groups = Number(report?.group_count || 0);
  const cheaper = Number(report?.groups_with_cheaper_sufficient_lane || 0);
  const unsatisfied = Number(report?.groups_without_sufficient_lane || 0);

  const groupsPill = $("evalLaneGroupPill");
  if (groupsPill) {
    groupsPill.textContent = `groups: ${groups}`;
    groupsPill.className = `pill ${groups > 0 ? "ok" : "warn"}`;
  }

  const cheaperPill = $("evalLaneCheaperPill");
  if (cheaperPill) {
    cheaperPill.textContent = `cheaper sufficient: ${cheaper} | no sufficient: ${unsatisfied}`;
    cheaperPill.className = `pill ${unsatisfied > 0 ? "warn" : "ok"}`;
  }
}

function renderLaneSufficiencyError(message) {
  const body = $("evalLaneBody");
  if (body) {
    body.innerHTML = `<tr><td colspan="7" class="muted">${escapeHtml(message)}</td></tr>`;
  }
  const groupsPill = $("evalLaneGroupPill");
  if (groupsPill) {
    groupsPill.textContent = "groups: error";
    groupsPill.className = "pill bad";
  }
  const cheaperPill = $("evalLaneCheaperPill");
  if (cheaperPill) {
    cheaperPill.textContent = "cheaper sufficient: error";
    cheaperPill.className = "pill bad";
  }
}

async function refreshLaneSufficiencyPanel() {
  const snapshot = $("evalLaneSnapshot");
  if (!snapshot) return;
  try {
    const query = buildLaneSufficiencyQuery();
    const report = await api(`/router/lane-sufficiency-report?${query}`);
    renderLaneSufficiencyRows(report?.groups || []);
    setLaneSufficiencyPills(report || {});
    snapshot.textContent = JSON.stringify(report || {}, null, 2);
  } catch (e) {
    renderLaneSufficiencyError(e.message || "lane sufficiency refresh failed");
    snapshot.textContent = JSON.stringify({ error: e.message || "lane sufficiency refresh failed" }, null, 2);
  }
}

async function refreshRunsTable() {
  const filters = getFilterInputValues();
  persistFiltersToUrl(filters);
  const query = buildRunsQuery(filters);
  const runsRes = await api(`/router/evaluations${query ? `?${query}` : ""}`);
  renderRunsTable(runsRes?.runs || []);
}

function renderSuitesTable(suites) {
  const body = $("evalSuiteListBody");
  if (!body) return;
  body.innerHTML = "";
  const rows = suites || [];
  if ($("evalSuiteCount")) $("evalSuiteCount").textContent = `${rows.length} suites`;

  if (!rows.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="6" class="muted">No suites saved.</td>';
    body.appendChild(tr);
    return;
  }

  for (const s of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(s.suite_name || "-")}</td>
      <td>${escapeHtml(s.suite_version || "-")}</td>
      <td>${escapeHtml(s.target_mode || "-")}</td>
      <td>${Array.isArray(s.cases) ? s.cases.length : 0}</td>
      <td class="mono">${escapeHtml(s.updated_ts || s.created_ts || "-")}</td>
      <td class="row"></td>
    `;

    const actions = tr.querySelector("td:last-child");
    const addBtn = (label, fn) => {
      const b = document.createElement("button");
      b.textContent = label;
      b.addEventListener("click", fn);
      actions.appendChild(b);
    };

    addBtn("Load", () => loadSuiteToEditor(s.suite_name, s.suite_version));
    addBtn("Rerun", () => rerunSuite(s.suite_name, s.suite_version));
    addBtn("Delete", () => deleteSuite(s.suite_name, s.suite_version));
    body.appendChild(tr);
  }
}

async function refreshSuites() {
  const res = await api("/router/evaluation-suites?limit=200");
  renderSuitesTable(res?.suites || []);
}

async function loadSuiteToEditor(suiteName, suiteVersion) {
  try {
    const suite = await api(`/router/evaluation-suites/${encodeURIComponent(suiteName)}/${encodeURIComponent(suiteVersion)}`);
    if ($("suiteName")) $("suiteName").value = suiteName;
    if ($("suiteVersion")) $("suiteVersion").value = suiteVersion;
    setSuiteEditor({
      target_mode: suite.target_mode,
      candidate_models: suite.candidate_models || [],
      variants: suite.variants || [],
      cases: suite.cases || [],
      case_pass_threshold_pct: suite.case_pass_threshold_pct,
      suite_pass_threshold_pct: suite.suite_pass_threshold_pct,
      metadata: suite.metadata || {},
    });

    const filters = getFilterInputValues();
    filters.suite_name = suiteName;
    filters.suite_version = suiteVersion;
    persistFiltersToUrl(filters);
  } catch (e) {
    setOut({ error: e.message, action: "suite_load", suite_name: suiteName, suite_version: suiteVersion });
  }
}

async function saveSuite() {
  const suiteName = $("suiteName")?.value?.trim();
  const suiteVersion = $("suiteVersion")?.value?.trim() || "1";
  if (!suiteName) {
    setOut({ error: "suite name required" });
    return;
  }
  try {
    const payload = parseSuiteEditor();
    setOut({ running: true, action: "suite_put", suite_name: suiteName, suite_version: suiteVersion });
    const result = await api(`/router/evaluation-suites/${encodeURIComponent(suiteName)}/${encodeURIComponent(suiteVersion)}`, {
      method: "PUT",
      body: {
        target_mode: payload.target_mode || "chat",
        candidate_models: Array.isArray(payload.candidate_models) ? payload.candidate_models : [],
        variants: Array.isArray(payload.variants) ? payload.variants : [],
        cases: Array.isArray(payload.cases) ? payload.cases : [],
        case_pass_threshold_pct: payload.case_pass_threshold_pct,
        suite_pass_threshold_pct: payload.suite_pass_threshold_pct,
        metadata: payload.metadata || {},
      },
    });
    setOut(result);
    await refreshSuites();
  } catch (e) {
    setOut({ error: e.message, action: "suite_put" });
  }
}

async function deleteSuite(suiteNameArg, suiteVersionArg) {
  const suiteName = suiteNameArg || $("suiteName")?.value?.trim();
  const suiteVersion = suiteVersionArg || $("suiteVersion")?.value?.trim() || "1";
  if (!suiteName) {
    setOut({ error: "suite name required" });
    return;
  }
  try {
    setOut({ running: true, action: "suite_delete", suite_name: suiteName, suite_version: suiteVersion });
    const result = await api(`/router/evaluation-suites/${encodeURIComponent(suiteName)}/${encodeURIComponent(suiteVersion)}`, {
      method: "DELETE",
    });
    setOut(result);
    await refreshSuites();
  } catch (e) {
    setOut({ error: e.message, action: "suite_delete", suite_name: suiteName, suite_version: suiteVersion });
  }
}

async function rerunSuite(suiteNameArg, suiteVersionArg) {
  const suiteName = suiteNameArg || $("suiteName")?.value?.trim();
  const suiteVersion = suiteVersionArg || $("suiteVersion")?.value?.trim() || "1";
  const asyncRun = !!$("suiteRunAsync")?.checked;
  const priority = $("suiteRunPriority")?.value || "evaluation";

  if (!suiteName) {
    setOut({ error: "suite name required" });
    return;
  }

  try {
    setOut({ running: true, action: "suite_rerun", suite_name: suiteName, suite_version: suiteVersion, asyncRun, priority });
    const result = await api(`/router/evaluation-suites/${encodeURIComponent(suiteName)}/${encodeURIComponent(suiteVersion)}/rerun`, {
      method: "POST",
      body: { async_run: asyncRun, priority },
    });
    setOut(result);
    await refreshEvalPanel();
  } catch (e) {
    setOut({ error: e.message, action: "suite_rerun", suite_name: suiteName, suite_version: suiteVersion });
  }
}

function loadSuiteTemplate() {
  const suiteName = $("suiteName")?.value?.trim() || "new_suite";
  const suiteVersion = $("suiteVersion")?.value?.trim() || "1";
  setSuiteEditor(defaultSuiteTemplate(suiteName, suiteVersion));
}

export async function evalRerunSuite() {
  const suite = $("evalSuiteName")?.value?.trim();
  const version = $("evalSuiteVersion")?.value?.trim() || "1";
  const asyncRun = !!$("evalRerunAsync")?.checked;
  const priority = $("evalPriority")?.value || "evaluation";
  if (!suite) return setOut({ error: "Suite name required" });

  try {
    setOut({ running: true, action: "rerun_suite", suite, version, asyncRun, priority });
    const result = await api(`/router/evaluation-suites/${encodeURIComponent(suite)}/${encodeURIComponent(version)}/rerun`, {
      method: "POST",
      body: { async_run: asyncRun, priority },
    });
    setOut(result);
    await refreshEvalPanel();
  } catch (e) {
    setOut({ error: e.message, action: "rerun_suite", suite, version });
  }
}

function resetFilters() {
  applyFilterValuesToInputs({ ...DEFAULT_FILTERS });
  const drawer = $("evalRunDrawer");
  if (drawer) drawer.dataset.runId = "";
  refreshRunsTable().catch((e) => setOut({ error: e.message, action: "eval_filter_reset" }));
}

function applyFilters() {
  refreshRunsTable().catch((e) => setOut({ error: e.message, action: "eval_filter_apply" }));
}

function applyPreset() {
  const presetKey = $("evalFilterPreset")?.value || "";
  const preset = PRESETS[presetKey] || {};
  const next = { ...DEFAULT_FILTERS, ...preset, preset: presetKey, run_id: "" };
  applyFilterValuesToInputs(next);
  const drawer = $("evalRunDrawer");
  if (drawer) drawer.dataset.runId = "";
  applyFilters();
}

function clearPresetAndApply() {
  if ($("evalFilterPreset")) $("evalFilterPreset").value = "";
  applyFilters();
}

export async function refreshEvalPanel() {
  const panel = $("evalSnapshot");
  if (!panel) return;

  try {
    const [q, s, w, completed] = await Promise.all([
      api("/router/evaluation-queue-state?limit=8"),
      api("/router/evaluation-summary?limit=5"),
      api("/router/evaluation-worker-config"),
      api("/router/evaluations?limit=10&status=completed"),
    ]);

    LAST_QUEUE = q;
    LAST_WORKERS = w;
    updateEvalContext();

    const suiteName = $("evalSuiteName")?.value?.trim();
    let latestRuns = [];
    if (suiteName) {
      try {
        const list = await api("/router/evaluations?limit=5&status=completed");
        latestRuns = (list.runs || []).filter((r) => (r.suite_name || "") === suiteName).slice(0, 5);
      } catch (_) {}
    }

    const recentRuns = (completed?.runs || []).slice(0, 5).map((r) => ({
      run_id: r.run_id,
      suite_name: r.suite_name,
      suite_version: r.suite_version,
      target_mode: r.target_mode,
      suite_pass: r.suite_pass,
      status: r.status,
      created_ts: r.created_ts,
    }));

    panel.textContent = JSON.stringify(
      {
        queue: {
          queued: q.queued_count,
          running: q.running_count,
          completed: q.completed_count,
          error: q.error_count,
          worker_count: q.worker_count,
          priority_running_caps: q.priority_running_caps,
        },
        workers: w,
        latest_reports: s.reports || [],
        next_queued: q.queued || [],
        latest_completed_runs: recentRuns,
        latest_runs_for_suite: latestRuns,
      },
      null,
      2
    );

    renderEvalRunActions(recentRuns);
    await Promise.all([refreshRunsTable(), refreshSuites(), refreshLaneSufficiencyPanel()]);
  } catch (e) {
    panel.textContent = JSON.stringify({ error: e.message }, null, 2);
    renderEvalRunActions([]);
    renderRunsTable([]);
    renderLaneSufficiencyError(e.message || "evaluation panel refresh failed");
  }
}

export function wireEvaluationDomain() {
  const loaded = loadFiltersFromUrl();
  applyFilterValuesToInputs(loaded);

  $("btnEvalRefresh")?.addEventListener("click", refreshEvalPanel);
  $("btnEvalLaneRefresh")?.addEventListener("click", () => refreshLaneSufficiencyPanel().catch((e) => setOut({ error: e.message, action: "lane_sufficiency_refresh" })));
  $("btnEvalRerun")?.addEventListener("click", evalRerunSuite);
  $("btnEvalApplyFilters")?.addEventListener("click", applyFilters);
  $("btnEvalResetFilters")?.addEventListener("click", resetFilters);
  $("btnEvalApplyPreset")?.addEventListener("click", applyPreset);

  [
    "evalLaneTargetMode",
    "evalLaneProject",
    "evalLaneSince",
    "evalLaneLimitGroups",
    "evalLaneMinRows",
    "evalLaneMinPassRate",
    "evalLaneMaxPassGap",
  ].forEach((id) => {
    $(id)?.addEventListener("change", () => {
      refreshLaneSufficiencyPanel().catch((e) => setOut({ error: e.message, action: "lane_sufficiency_refresh" }));
    });
  });

  $("btnSuiteRefresh")?.addEventListener("click", () => refreshSuites().catch((e) => setOut({ error: e.message, action: "suite_refresh" })));
  $("btnSuiteTemplate")?.addEventListener("click", loadSuiteTemplate);
  $("btnSuiteSave")?.addEventListener("click", saveSuite);
  $("btnSuiteDelete")?.addEventListener("click", () => deleteSuite());
  $("btnSuiteRerunSelected")?.addEventListener("click", () => rerunSuite());

  $("btnEvalDrawerClose")?.addEventListener("click", closeEvalRunDetail);
  $("evalDrawerBackdrop")?.addEventListener("click", closeEvalRunDetail);

  [
    "evalFilterStatus",
    "evalFilterMode",
    "evalFilterProject",
    "evalFilterProvider",
    "evalFilterLane",
    "evalFilterModel",
    "evalFilterTag",
    "evalFilterSince",
    "evalFilterPass",
    "evalFilterLimit",
  ].forEach((id) => {
    $(id)?.addEventListener("change", clearPresetAndApply);
  });

  const runId = loaded.run_id || "";
  if (runId) {
    openEvalRunDetail(runId).catch((e) => setOut({ error: e.message, action: "eval_open_run_detail", run_id: runId }));
  }

  if (!$("suiteEditor")?.value?.trim()) {
    loadSuiteTemplate();
  }
}
