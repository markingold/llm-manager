import { api } from "../api.js?v=20260722_3";
import { $, escapeHtml, setOut } from "../ui-core.js?v=20260722_3";

const DOCS = {
  models: null,
  policies: null,
};

const VERSIONS = {
  models: "",
  policies: "",
};

let CURATED_ROWS = [];

function selectedResource() {
  return $("govResource")?.value || "models";
}

function setVersionsUI() {
  if ($("govModelsVersion")) {
    $("govModelsVersion").textContent = `models version: ${VERSIONS.models || "-"}`;
  }
  if ($("govPoliciesVersion")) {
    $("govPoliciesVersion").textContent = `policies version: ${VERSIONS.policies || "-"}`;
  }
}

function setEditorFromResource() {
  const res = selectedResource();
  if ($("govEditor")) {
    $("govEditor").value = JSON.stringify(DOCS[res] || {}, null, 2);
  }
}

function parseEditorDoc() {
  const raw = $("govEditor")?.value?.trim() || "";
  if (!raw) throw new Error("governance editor is empty");
  const parsed = JSON.parse(raw);
  if (!parsed || typeof parsed !== "object") throw new Error("governance document must be a JSON object");
  return parsed;
}

function renderAudit(rows) {
  const body = $("govAuditBody");
  if (!body) return;
  body.innerHTML = "";

  const entries = Array.isArray(rows) ? [...rows].reverse().slice(0, 120) : [];
  if (!entries.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="7" class="muted">No governance audit entries.</td>';
    body.appendChild(tr);
    return;
  }

  for (const e of entries) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono">${escapeHtml(e.ts || "-")}</td>
      <td>${escapeHtml(e.resource || "-")}</td>
      <td>${escapeHtml(e.action || "-")}</td>
      <td>${escapeHtml(e.actor || "-")}</td>
      <td>${escapeHtml(e.outcome || "-")}</td>
      <td>${escapeHtml(e.reason || "-")}</td>
      <td class="mono">${escapeHtml(e.before_version || "-")} -> ${escapeHtml(e.after_version || "-")}</td>
    `;
    body.appendChild(tr);
  }
}

function curatedFilters() {
  return {
    provider: $("curatedProviderFilter")?.value?.trim() || "",
    bucket: $("curatedBucketFilter")?.value?.trim() || "",
    search: $("curatedSearch")?.value?.trim() || "",
    enabled_only: !!$("curatedEnabledOnly")?.checked,
  };
}

function renderCuratedRows(rows) {
  const body = $("curatedModelsBody");
  if (!body) return;

  CURATED_ROWS = Array.isArray(rows) ? rows : [];
  if (!CURATED_ROWS.length) {
    body.innerHTML = '<tr><td colspan="7" class="muted">No curated model rows found for current filters.</td></tr>';
    return;
  }

  body.innerHTML = CURATED_ROWS.map((row, idx) => {
    const backendEditable = String(row?.provider || "") === "local";
    const runtime = row?.runtime || {};
    const runtimeText = [
      runtime?.promotion_state ? `state=${runtime.promotion_state}` : null,
      `f24=${Number(runtime?.failure_count_24h || 0)}`,
      `f7d=${Number(runtime?.failure_count_7d || 0)}`,
      runtime?.disabled_until_manual_review ? "manual_review=true" : null,
      runtime?.exclude_from_free_rotation ? "excluded=true" : null,
    ].filter(Boolean).join(" | ");
    const currentPriority = Number.isFinite(Number(row?.priority)) ? String(Number(row.priority)) : "";

    return `
      <tr data-curated-row="${idx}">
        <td>${escapeHtml(String(row?.provider || "-"))}/${escapeHtml(String(row?.bucket || "-"))}</td>
        <td>
          <div class="mono">${escapeHtml(String(row?.model_id || "-"))}</div>
          <div class="muted">${escapeHtml(String(row?.label || ""))}</div>
        </td>
        <td><input type="checkbox" data-curated-field="enabled" ${row?.enabled ? "checked" : ""} /></td>
        <td><input data-curated-field="priority" type="number" step="1" value="${escapeHtml(currentPriority)}" style="max-width:100px" /></td>
        <td><input data-curated-field="backend" value="${escapeHtml(String(row?.backend || ""))}" ${backendEditable ? "" : "disabled"} style="max-width:170px" /></td>
        <td class="mono">${escapeHtml(runtimeText || "-")}</td>
        <td><button data-curated-apply="${idx}">Apply</button></td>
      </tr>
    `;
  }).join("");

  body.querySelectorAll("button[data-curated-apply]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.getAttribute("data-curated-apply") || "-1");
      applyCuratedRowUpdate(idx);
    });
  });
}

async function refreshCuratedModelRows() {
  const body = $("curatedModelsBody");
  if (!body) return;

  try {
    const filters = curatedFilters();
    const params = new URLSearchParams();
    if (filters.provider) params.set("provider", filters.provider);
    if (filters.bucket) params.set("bucket", filters.bucket);
    if (filters.search) params.set("search", filters.search);
    if (filters.enabled_only) params.set("enabled_only", "true");
    params.set("limit", "400");

    const res = await api(`/providers/models/curated-summary?${params.toString()}`);
    renderCuratedRows(Array.isArray(res?.rows) ? res.rows : []);
  } catch (e) {
    body.innerHTML = `<tr><td colspan="7" class="muted">${escapeHtml(e.message || "Failed to load curated rows")}</td></tr>`;
  }
}

async function applyCuratedRowUpdate(index) {
  const row = CURATED_ROWS[index];
  if (!row) return;

  const actor = $("govActor")?.value?.trim() || "webui";
  const reason = $("govReason")?.value?.trim() || "";
  if (!reason) {
    setOut({ error: "reason is required for curated model updates" });
    return;
  }

  const tr = document.querySelector(`tr[data-curated-row="${index}"]`);
  if (!tr) return;

  const enabled = !!tr.querySelector('[data-curated-field="enabled"]')?.checked;
  const priorityRaw = tr.querySelector('[data-curated-field="priority"]')?.value?.trim() || "";
  const priority = priorityRaw ? Number(priorityRaw) : null;
  const backendRaw = tr.querySelector('[data-curated-field="backend"]')?.value?.trim() || "";

  const payload = {
    provider: row.provider,
    bucket: row.bucket,
    model_id: row.model_id,
    enabled,
    priority: Number.isFinite(priority) ? Number(priority) : null,
    reason,
    actor,
  };
  if (String(row.provider) === "local") {
    payload.backend = backendRaw || null;
  }

  try {
    setOut({ running: true, action: "curated_model_update", payload });
    const result = await api("/providers/models/curated-entry", {
      method: "POST",
      body: payload,
    });
    setOut(result);
    await refreshGovernancePanel();
  } catch (e) {
    setOut({ error: e.message, action: "curated_model_update", payload });
  }
}

export async function refreshGovernancePanel() {
  try {
    const [modelsRes, policiesRes, stateRes] = await Promise.all([
      api("/providers/models"),
      api("/providers/policies"),
      api("/providers/state"),
    ]);

    DOCS.models = modelsRes?.models || {};
    DOCS.policies = policiesRes?.policies || {};
    VERSIONS.models = modelsRes?.version || "";
    VERSIONS.policies = policiesRes?.version || "";

    setVersionsUI();
    setEditorFromResource();

    const state = stateRes?.state || {};
    renderAudit(state?.governance_audit || []);
    await refreshCuratedModelRows();
  } catch (e) {
    setOut({ error: e.message, action: "governance_refresh" });
  }
}

async function governanceWrite(validateOnly) {
  const resource = selectedResource();
  const reason = $("govReason")?.value?.trim() || "";
  const actor = $("govActor")?.value?.trim() || "webui";
  const expectedVersion = VERSIONS[resource] || "";

  if (!validateOnly && !reason) {
    setOut({ error: "reason is required for apply" });
    return;
  }

  if (!validateOnly && $("govRequireConfirm")?.checked) {
    const ok = window.confirm(`Apply ${resource} governance changes?`);
    if (!ok) return;
  }

  try {
    const document = parseEditorDoc();
    setOut({ running: true, action: validateOnly ? "governance_validate" : "governance_apply", resource });
    const result = await api(`/providers/${resource}`, {
      method: "PUT",
      body: {
        document,
        expected_version: expectedVersion || null,
        validate_only: !!validateOnly,
        reason,
        actor,
      },
    });
    setOut(result);
    await refreshGovernancePanel();
  } catch (e) {
    setOut({ error: e.message, action: validateOnly ? "governance_validate" : "governance_apply", resource });
  }
}

async function governanceRollback() {
  const resource = selectedResource();
  const reason = $("govReason")?.value?.trim() || "";
  const actor = $("govActor")?.value?.trim() || "webui";
  const expectedVersion = VERSIONS[resource] || "";

  if (!reason) {
    setOut({ error: "reason is required for rollback" });
    return;
  }

  if ($("govRequireConfirm")?.checked) {
    const ok = window.confirm(`Rollback ${resource} to last good snapshot?`);
    if (!ok) return;
  }

  try {
    setOut({ running: true, action: "governance_rollback", resource });
    const result = await api(`/providers/${resource}/rollback`, {
      method: "POST",
      body: {
        expected_version: expectedVersion || null,
        reason,
        actor,
      },
    });
    setOut(result);
    await refreshGovernancePanel();
  } catch (e) {
    setOut({ error: e.message, action: "governance_rollback", resource });
  }
}

async function applyProviderFlags() {
  const modelKey = $("govFlagModelKey")?.value?.trim() || "";
  const reason = $("govReason")?.value?.trim() || "";
  const actor = $("govActor")?.value?.trim() || "webui";
  if (!modelKey) {
    setOut({ error: "provider:model_id model key is required" });
    return;
  }
  if (!reason) {
    setOut({ error: "reason is required for flags update" });
    return;
  }

  if ($("govRequireConfirm")?.checked) {
    const ok = window.confirm(`Apply provider-model flags for ${modelKey}?`);
    if (!ok) return;
  }

  try {
    setOut({ running: true, action: "governance_flags_update", model_key: modelKey });
    const result = await api("/providers/state/provider-model-flags", {
      method: "POST",
      body: {
        model_key: modelKey,
        disabled_until_manual_review: !!$("govFlagDisabled")?.checked,
        exclude_from_free_rotation: !!$("govFlagExclude")?.checked,
        reason,
        actor,
      },
    });
    setOut(result);
    await refreshGovernancePanel();
  } catch (e) {
    setOut({ error: e.message, action: "governance_flags_update", model_key: modelKey });
  }
}

export function wireGovernanceDomain() {
  $("govResource")?.addEventListener("change", setEditorFromResource);
  $("btnGovRefresh")?.addEventListener("click", refreshGovernancePanel);
  $("btnGovValidate")?.addEventListener("click", () => governanceWrite(true));
  $("btnGovApply")?.addEventListener("click", () => governanceWrite(false));
  $("btnGovRollback")?.addEventListener("click", governanceRollback);
  $("btnGovApplyFlags")?.addEventListener("click", applyProviderFlags);
  $("btnCuratedRefresh")?.addEventListener("click", refreshCuratedModelRows);

  ["curatedProviderFilter", "curatedBucketFilter", "curatedEnabledOnly"].forEach((id) => {
    $(id)?.addEventListener("change", refreshCuratedModelRows);
  });
  $("curatedSearch")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      refreshCuratedModelRows();
    }
  });

  refreshGovernancePanel();
}
