import { api } from "../api.js?v=20260722_2";
import { $, setOut, setBudgetBanner } from "../ui-core.js?v=20260722_2";
import { pickFlaggedModels, updateBudgetBannerFromSnapshot } from "./providers-budget.js?v=20260722_2";

function parseCsvList(value) {
  return String(value || "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function parseOptionalNumber(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return null;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseOptionalRequiredFlag(value) {
  const raw = String(value ?? "").trim().toLowerCase();
  if (!raw) return null;
  return raw === "true" ? true : null;
}

function setRouterManualSnapshot(payload) {
  const panel = $("routerManualSnapshot");
  if (!panel) return;
  panel.textContent = JSON.stringify(payload || {}, null, 2);
}

function setRouterDiscoverResult(payload) {
  const panel = $("routerDiscoverResult");
  if (!panel) return;
  panel.textContent = JSON.stringify(payload || {}, null, 2);
}

function setRouterDecisionSnapshot(payload) {
  const panel = $("routerDecisionSnapshot");
  if (!panel) return;
  panel.textContent = JSON.stringify(payload || {}, null, 2);
}

function currentDiscoveryPayload(overrides = {}) {
  return {
    refresh_catalog: !!$("routerDiscoverRefreshCatalog")?.checked,
    include_rankings: !!$("routerDiscoverIncludeRankings")?.checked,
    include_curated: !!$("routerDiscoverIncludeCurated")?.checked,
    activate_top_n: Number($("routerDiscoverActivateTopN")?.value || 0) || 0,
    clear_active_ids: false,
    max_candidates: Number($("routerDiscoverMaxCandidates")?.value || 20) || 20,
    sort_by: $("routerDiscoverSortBy")?.value || "score",
    min_context_length: parseOptionalNumber($("routerDiscoverMinContext")?.value),
    max_total_params_b: parseOptionalNumber($("routerDiscoverMaxTotalParams")?.value),
    max_active_params_b: parseOptionalNumber($("routerDiscoverMaxActiveParams")?.value),
    min_popularity_tokens: parseOptionalNumber($("routerDiscoverMinPopularityTokens")?.value),
    allow_unknown_size: !!$("routerDiscoverAllowUnknownSize")?.checked,
    require_tools: parseOptionalRequiredFlag($("routerDiscoverRequireTools")?.value),
    require_structured_outputs: parseOptionalRequiredFlag($("routerDiscoverRequireStructured")?.value),
    require_reasoning: parseOptionalRequiredFlag($("routerDiscoverRequireReasoning")?.value),
    require_vision: parseOptionalRequiredFlag($("routerDiscoverRequireVision")?.value),
    family_allow: parseCsvList($("routerDiscoverFamilyAllow")?.value),
    family_deny: parseCsvList($("routerDiscoverFamilyDeny")?.value),
    actor: $("routerDiscoverActor")?.value?.trim() || "webui",
    reason: $("routerDiscoverReason")?.value?.trim() || "",
    ...overrides,
  };
}

export async function refreshRouterPanel() {
  const panel = $("routerSnapshot");
  if (!panel) return;

  try {
    const [health, fallback, usage, queue, budget, pstate, freeCandidates, decisionTraces] = await Promise.all([
      api("/router/health"),
      api("/router/fallback-stats?limit=500"),
      api("/router/usage-summary?limit=500"),
      api("/router/queue-state"),
      api("/router/budget-state"),
      api("/providers/state"),
      api("/providers/openrouter/free-candidates"),
      api("/router/decision-traces?limit=40&compact=true"),
    ]);

    const state = pstate?.state || {};
    const providerModelState = state.provider_model_state || {};
    const flaggedModels = pickFlaggedModels(providerModelState, 20);

    const openrouterCatalog = state.openrouter_catalog_cache || {};
    const snapshot = {
      router_health: health?.router || {},
      fallback_stats: fallback,
      usage_summary: usage,
      free_tier_queue: {
        depth: queue?.queue_depth,
        pending: queue?.pending_count,
        expired: queue?.expired_count,
      },
      budget,
      openrouter_catalog: {
        fetched_ts: openrouterCatalog.fetched_ts,
        count: openrouterCatalog.count,
        free_count: (openrouterCatalog.free_ids || []).length,
        error: openrouterCatalog.error,
      },
      openrouter_manual_free_candidates: freeCandidates?.free_candidates || {},
      route_decision_traces: {
        count: Number(decisionTraces?.count || 0),
        summary: decisionTraces?.summary || {},
      },
      flagged_provider_models: flaggedModels,
    };

    const healthPill = $("routerHealthPill");
    if (healthPill) {
      const h = snapshot.router_health || {};
      const ok = h.config_loaded !== false;
      healthPill.textContent = `health: ${ok ? "ok" : "degraded"}`;
      healthPill.className = `pill ${ok ? "ok" : "warn"}`;
    }

    const fallbackPill = $("routerFallbackPill");
    if (fallbackPill) {
      const withErrors = Number(fallback?.with_attempt_errors || 0);
      const withSelectedFallback = Number(fallback?.with_selected_fallback || 0);
      fallbackPill.textContent = `fallbacks/errors: ${withSelectedFallback}/${withErrors}`;
      fallbackPill.className = `pill ${withErrors > 0 || withSelectedFallback > 0 ? "warn" : "ok"}`;
    }

    const queuePill = $("routerQueuePill");
    if (queuePill) {
      const depth = Number(queue?.queue_depth || 0);
      queuePill.textContent = `queue: depth=${depth}`;
      queuePill.className = `pill ${depth > 0 ? "warn" : "ok"}`;
    }

    const budgetPill = $("routerBudgetPill");
    if (budgetPill) {
      const exceeded = !!(budget?.daily_exceeded || budget?.monthly_exceeded);
      const warn = !!(budget?.daily_warn || budget?.monthly_warn);
      const tone = exceeded ? "bad" : warn ? "warn" : "ok";
      budgetPill.textContent = `budget: ${exceeded ? "exceeded" : warn ? "warn" : "ok"}`;
      budgetPill.className = `pill ${tone}`;
    }

    const backendPill = $("routerBackendPill");
    if (backendPill) {
      const byBackend = decisionTraces?.summary?.by_backend || {};
      const pairs = Object.entries(byBackend).sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0));
      const compactMix = pairs.slice(0, 3).map(([k, v]) => `${k}:${v}`).join(" ");
      backendPill.textContent = `backend mix: ${compactMix || "n/a"}`;
      backendPill.className = `pill ${pairs.length > 0 ? "ok" : "warn"}`;
    }

    const taskPill = $("routerTaskPill");
    if (taskPill) {
      const byTaskType = decisionTraces?.summary?.by_task_type || {};
      const pairs = Object.entries(byTaskType).sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0));
      const compactMix = pairs.slice(0, 3).map(([k, v]) => `${k}:${v}`).join(" ");
      taskPill.textContent = `tasks: ${compactMix || "n/a"}`;
      taskPill.className = `pill ${pairs.length > 0 ? "ok" : "warn"}`;
    }

    const reasonPill = $("routerReasonPill");
    if (reasonPill) {
      const byReasonCode = decisionTraces?.summary?.by_reason_code || {};
      const reasonPairs = Object.entries(byReasonCode)
        .filter(([reason]) => String(reason || "") !== "selected")
        .sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0));
      const compactReasons = reasonPairs.slice(0, 3).map(([k, v]) => `${k}:${v}`).join(" ");
      reasonPill.textContent = `reasons: ${compactReasons || "none"}`;
      reasonPill.className = `pill ${reasonPairs.length > 0 ? "warn" : "ok"}`;
    }

    const selectedFallbackPill = $("routerSelectedFallbackPill");
    if (selectedFallbackPill) {
      const selectedFallbackCount = Number(decisionTraces?.summary?.with_selected_fallback || fallback?.with_selected_fallback || 0);
      const traceCount = Number(decisionTraces?.count || 0);
      selectedFallbackPill.textContent = `selected fallback: ${selectedFallbackCount}${traceCount > 0 ? `/${traceCount}` : ""}`;
      selectedFallbackPill.className = `pill ${selectedFallbackCount > 0 ? "warn" : "ok"}`;
    }

    const manualCandidates = freeCandidates?.free_candidates || {};
    const candidateCount = Number(manualCandidates?.candidate_count || (manualCandidates?.candidates || []).length || 0);
    const activeCount = Array.isArray(manualCandidates?.active_ids) ? manualCandidates.active_ids.length : 0;

    const manualCandidatesPill = $("routerManualCandidatesPill");
    if (manualCandidatesPill) {
      manualCandidatesPill.textContent = `manual candidates: ${candidateCount}`;
      manualCandidatesPill.className = `pill ${candidateCount > 0 ? "ok" : "warn"}`;
    }

    const manualActivePill = $("routerManualActivePill");
    if (manualActivePill) {
      manualActivePill.textContent = `manual active: ${activeCount}`;
      manualActivePill.className = `pill ${activeCount > 0 ? "ok" : "warn"}`;
    }

    panel.textContent = JSON.stringify(snapshot, null, 2);
    setRouterManualSnapshot(freeCandidates || {});
    setRouterDecisionSnapshot(decisionTraces || {});
    updateBudgetBannerFromSnapshot(budget, setBudgetBanner);
  } catch (e) {
    panel.textContent = JSON.stringify({ error: e.message }, null, 2);
    setRouterManualSnapshot({ error: e.message });
    setRouterDecisionSnapshot({ error: e.message });
    setBudgetBanner("");
  }
}

export async function refreshOpenRouterCatalog() {
  try {
    const includeRankings = !!$("routerCatalogIncludeRankings")?.checked;
    setOut({ running: true, action: "refresh_openrouter_catalog", include_rankings: includeRankings });
    const result = await api(`/providers/openrouter/refresh?include_rankings=${includeRankings ? "true" : "false"}`, { method: "POST" });
    setOut(result);
    await refreshRouterPanel();
  } catch (e) {
    setOut({ error: e.message, action: "refresh_openrouter_catalog" });
  }
}

export async function refreshOpenRouterFreeCandidates() {
  try {
    const result = await api("/providers/openrouter/free-candidates");
    setRouterManualSnapshot(result);
    setOut(result);
    await refreshRouterPanel();
  } catch (e) {
    const payload = { error: e.message, action: "refresh_openrouter_free_candidates" };
    setRouterManualSnapshot(payload);
    setOut(payload);
  }
}

export async function discoverOpenRouterFreeCandidates(overrides = {}) {
  const payload = currentDiscoveryPayload(overrides);
  try {
    setOut({ running: true, action: "discover_openrouter_free_candidates", payload });
    const result = await api("/providers/openrouter/discover-free", {
      method: "POST",
      body: payload,
    });
    setOut(result);
    setRouterDiscoverResult(result);
    await refreshRouterPanel();
  } catch (e) {
    const errorPayload = { error: e.message, action: "discover_openrouter_free_candidates", payload };
    setOut(errorPayload);
    setRouterDiscoverResult(errorPayload);
  }
}

export function wireRouterDomain() {
  $("btnRouterRefresh")?.addEventListener("click", refreshRouterPanel);
  $("btnRouterCatalogRefresh")?.addEventListener("click", refreshOpenRouterCatalog);
  $("btnRouterFreeCandidatesRefresh")?.addEventListener("click", refreshOpenRouterFreeCandidates);
  $("btnRouterDecisionTraceRefresh")?.addEventListener("click", refreshRouterPanel);
  $("btnRouterDiscoverFree")?.addEventListener("click", () => discoverOpenRouterFreeCandidates());
  $("btnRouterClearActive")?.addEventListener("click", () => discoverOpenRouterFreeCandidates({
    activate_top_n: 0,
    clear_active_ids: true,
  }));
}
